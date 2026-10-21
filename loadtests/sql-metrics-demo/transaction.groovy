import java.sql.DriverManager
import java.sql.SQLException
import java.util.concurrent.ThreadLocalRandom

vars.remove('bootstrap_token')
def host = System.getenv('SQL_HOST')
def database = System.getenv('SQL_DATABASE')
def runToken = System.getenv('SQL_RUN_TOKEN')
if (!(host ==~ /sql-metrics-[a-f0-9]{10}\.database\.windows\.net/) ||
    database != 'metrics-demo' || runToken == null ||
    props.get('sql.metrics.token') == null) {
    SampleResult.setResponseCode('CONFIG_ERROR')
    SampleResult.setResponseMessage('Missing or invalid isolated SQL demo configuration')
    SampleResult.setSuccessful(false)
    SampleResult.setStopTest(true)
    return
}
try {
    UUID.fromString(runToken)
    def connection = vars.getObject('sql.metrics.connection')
    if (connection == null) {
        Class.forName('com.microsoft.sqlserver.jdbc.SQLServerDriver')
        def options = new Properties()
        options.setProperty('accessToken', props.get('sql.metrics.token').toString())
        connection = DriverManager.getConnection(
            "jdbc:sqlserver://${host}:1433;databaseName=${database};encrypt=true;" +
            'trustServerCertificate=false;loginTimeout=15;socketTimeout=20000;cancelQueryTimeout=5;' +
            'applicationName=isolated-sql-metrics;connectRetryCount=0', options)
        vars.putObject('sql.metrics.connection', connection)
        props.get('sql.metrics.connections').add(connection)
    }
    connection.prepareCall('{call dbo.DemoTransaction(?, ?)}').withCloseable { statement ->
        statement.setString(1, runToken)
        statement.setInt(2, ThreadLocalRandom.current().nextInt(1, 513))
        statement.setQueryTimeout(10)
        statement.executeQuery().withCloseable { rows ->
            if (!rows.next() || !rows.getString('run_token').equalsIgnoreCase(runToken) ||
                rows.getLong('completed_operations') < 1 || rows.getInt('account_count') != 512 ||
                rows.next()) {
                throw new IllegalStateException('SQL response failed bounded-work assertions')
            }
        }
    }
    SampleResult.setResponseCode('200')
    SampleResult.setResponseMessage('Committed SQL transaction and checked 512-row aggregate')
    SampleResult.setResponseData('One committed SQL operation', 'UTF-8')
    SampleResult.setSuccessful(true)
} catch (SQLException error) {
    SampleResult.setResponseCode('SQL_ERROR')
    SampleResult.setResponseMessage("SQL failed: state=${error.SQLState}, code=${error.errorCode}")
    SampleResult.setSuccessful(false)
    SampleResult.setStopTest(true)
} catch (IllegalArgumentException | IllegalStateException error) {
    SampleResult.setResponseCode('ASSERTION_ERROR')
    SampleResult.setResponseMessage('SQL run-token or result assertion failed')
    SampleResult.setSuccessful(false)
    SampleResult.setStopTest(true)
}
