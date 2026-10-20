import groovy.json.JsonOutput
import java.sql.DriverManager
import java.sql.SQLException

def host = System.getenv('SQL_HOST')
def runToken = System.getenv('SQL_RUN_TOKEN')
def engineSid = System.getenv('SQL_ENGINE_SID')
def mode = Parameters.trim()
if (mode == 'verify') {
    def connections = props.remove('sql.metrics.connections')
    if (connections != null) {
        connections.each { it.close() }
    }
    props.remove('sql.metrics.token')
}
def token = vars.get('bootstrap_token')
if (!(host ==~ /sql-metrics-[a-f0-9]{10}\.database\.windows\.net/) ||
    System.getenv('SQL_DATABASE') != 'metrics-demo' ||
    !(engineSid ==~ /[a-f0-9]{32}/) || token == null || !token.startsWith('eyJ') ||
    !(mode in ['initialize', 'verify'])) {
    SampleResult.setResponseCode('CONFIG_ERROR')
    SampleResult.setResponseMessage('Isolated SQL bootstrap configuration or secret is unavailable')
    SampleResult.setSuccessful(false)
    SampleResult.setStopTest(true)
    vars.remove('bootstrap_token')
    return
}
try {
    UUID.fromString(runToken)
    Class.forName('com.microsoft.sqlserver.jdbc.SQLServerDriver')
    def options = new Properties()
    options.setProperty('accessToken', token)
    DriverManager.getConnection(
        "jdbc:sqlserver://${host}:1433;databaseName=metrics-demo;encrypt=true;" +
        'trustServerCertificate=false;loginTimeout=15;socketTimeout=40000;' +
        'cancelQueryTimeout=5;applicationName=sql-metrics-bootstrap;connectRetryCount=0',
        options
    ).withCloseable { connection ->
        if (mode == 'initialize') {
            def schema = new File('schema.sql').getText('UTF-8').replace('__ENGINE_SID__', engineSid)
            schema.split('(?im)^GO\\s*$').each { batch ->
                connection.createStatement().withCloseable { statement ->
                    statement.setQueryTimeout(30)
                    statement.execute(batch)
                }
            }
            connection.setAutoCommit(false)
            connection.createStatement().withCloseable { statement ->
                statement.setQueryTimeout(15)
                statement.executeUpdate('UPDATE dbo.DemoAccount SET balance = 100000')
                statement.executeUpdate('DELETE dbo.DemoRun')
            }
            connection.prepareStatement(
                'INSERT dbo.DemoRun(singleton, run_token, completed_operations) VALUES (1, ?, 0)'
            ).withCloseable { statement ->
                statement.setQueryTimeout(15)
                statement.setString(1, runToken)
                statement.executeUpdate()
            }
            connection.commit()
        } else {
            connection.prepareStatement(
                'SELECT CONVERT(varchar(36), run_token) AS run_token, completed_operations, ' +
                '(SELECT COUNT(*) FROM dbo.DemoAccount) AS account_count, ' +
                '(SELECT SUM(balance) - 51200000 FROM dbo.DemoAccount) AS balance_delta ' +
                'FROM dbo.DemoRun WHERE singleton = 1 AND run_token = ?'
            ).withCloseable { statement ->
                statement.setQueryTimeout(15)
                statement.setString(1, runToken)
                statement.executeQuery().withCloseable { rows ->
                    if (!rows.next() || !rows.getString('run_token').equalsIgnoreCase(runToken)) {
                        throw new IllegalStateException('SQL run evidence is missing')
                    }
                    def evidence = [
                        run_token: runToken,
                        completed_operations: rows.getInt('completed_operations'),
                        account_count: rows.getInt('account_count'),
                        balance_delta: rows.getLong('balance_delta')
                    ]
                    if (evidence.account_count != 512 || evidence.completed_operations < 1 ||
                        evidence.completed_operations > 240 ||
                        evidence.balance_delta != evidence.completed_operations || rows.next()) {
                        throw new IllegalStateException('SQL committed-work verification failed')
                    }
                    SampleResult.setResponseMessage('SQL_METRICS_EVIDENCE ' + JsonOutput.toJson(evidence))
                }
            }
        }
    }
    SampleResult.setResponseCode('200')
    if (mode == 'initialize') {
        SampleResult.setResponseMessage('Bounded SQL bootstrap initialize completed')
    }
    SampleResult.setSuccessful(true)
} catch (SQLException error) {
    SampleResult.setResponseCode('SQL_BOOTSTRAP_ERROR')
    SampleResult.setResponseMessage("SQL bootstrap failed: state=${error.SQLState}, code=${error.errorCode}")
    SampleResult.setSuccessful(false)
    SampleResult.setStopTest(true)
} catch (IOException | IllegalArgumentException | IllegalStateException error) {
    SampleResult.setResponseCode('SQL_BOOTSTRAP_ASSERTION')
    SampleResult.setResponseMessage('SQL bootstrap configuration or bounded-work verification failed')
    SampleResult.setSuccessful(false)
    SampleResult.setStopTest(true)
} finally {
    vars.remove('bootstrap_token')
}
