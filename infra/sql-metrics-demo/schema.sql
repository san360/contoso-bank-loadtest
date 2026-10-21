SET NOCOUNT ON;
SET XACT_ABORT ON;
IF DB_NAME() <> N'metrics-demo'
    THROW 50001, 'This schema belongs only in the isolated metrics-demo database.', 1;
IF OBJECT_ID(N'dbo.DemoAccount', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.DemoAccount (
        account_id int NOT NULL PRIMARY KEY CHECK (account_id BETWEEN 1 AND 512),
        balance bigint NOT NULL CHECK (balance BETWEEN 100000 AND 100240),
        payload char(256) NOT NULL
    );
    WITH digits(n) AS (
        SELECT n FROM (VALUES (0),(1),(2),(3),(4),(5),(6),(7)) AS d(n)
    )
    INSERT dbo.DemoAccount(account_id, balance, payload)
    SELECT a.n * 64 + b.n * 8 + c.n + 1, 100000, REPLICATE('M', 256)
    FROM digits a CROSS JOIN digits b CROSS JOIN digits c;
END;
IF OBJECT_ID(N'dbo.DemoRun', N'U') IS NULL
    CREATE TABLE dbo.DemoRun (
        singleton tinyint NOT NULL PRIMARY KEY CHECK (singleton = 1),
        run_token uniqueidentifier NOT NULL,
        completed_operations int NOT NULL CHECK (completed_operations BETWEEN 0 AND 240)
    );
DROP USER IF EXISTS [sql_metrics_engine];
CREATE USER [sql_metrics_engine] WITH SID = 0x__ENGINE_SID__, TYPE = E;
IF NOT EXISTS (
    SELECT 1 FROM sys.database_principals
    WHERE name = N'sql_metrics_engine' AND sid = 0x__ENGINE_SID__ AND type = 'E'
)
    THROW 50002, 'The existing demo user has an unexpected identity.', 1;
GO
CREATE OR ALTER PROCEDURE dbo.DemoTransaction
    @run_token uniqueidentifier,
    @account_id int
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    SET LOCK_TIMEOUT 5000;
    IF @account_id IS NULL OR @account_id NOT BETWEEN 1 AND 512
        THROW 50003, 'Account is outside the bounded demo dataset.', 1;
    BEGIN TRANSACTION;
    UPDATE dbo.DemoRun
        SET completed_operations = completed_operations + 1
        WHERE singleton = 1 AND run_token = @run_token;
    IF @@ROWCOUNT <> 1
    BEGIN
        ROLLBACK;
        THROW 50004, 'Run token was not initialized by the demo operator.', 1;
    END;
    UPDATE dbo.DemoAccount SET balance = balance + 1 WHERE account_id = @account_id;
    COMMIT;
    SELECT @run_token AS run_token,
           (SELECT completed_operations FROM dbo.DemoRun WHERE singleton = 1) AS completed_operations,
           COUNT(*) AS account_count,
           SUM(balance) AS total_balance,
           CHECKSUM_AGG(BINARY_CHECKSUM(account_id, balance, payload)) AS dataset_checksum
    FROM dbo.DemoAccount;
END;
GO
GRANT CONNECT TO [sql_metrics_engine];
GRANT EXECUTE ON OBJECT::dbo.DemoTransaction TO [sql_metrics_engine];
