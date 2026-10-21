import groovy.json.JsonSlurper
import groovy.json.JsonException
import java.util.concurrent.CopyOnWriteArrayList

def endpoint = new URL('http://169.254.169.254/metadata/identity/oauth2/token' +
    '?api-version=2018-02-01&resource=https%3A%2F%2Fdatabase.windows.net%2F')
def request = endpoint.openConnection(Proxy.NO_PROXY)
request.setRequestProperty('Metadata', 'true')
request.setConnectTimeout(5000)
request.setReadTimeout(5000)
request.setInstanceFollowRedirects(false)
try {
    if (request.responseCode != 200) {
        throw new IllegalStateException('Managed identity endpoint did not return HTTP 200')
    }
    def identity = request.inputStream.withCloseable { new JsonSlurper().parse(it) }
    if (!(identity instanceof Map) || !(identity.access_token instanceof String) ||
        identity.access_token.isEmpty() || identity.expires_on == null ||
        Long.parseLong(identity.expires_on.toString()) < System.currentTimeMillis() / 1000L + 300L) {
        throw new IllegalStateException('Managed identity returned no usable SQL token')
    }
    props.put('sql.metrics.token', identity.access_token)
    props.put('sql.metrics.connections', new CopyOnWriteArrayList())
    SampleResult.setResponseCode('200')
    SampleResult.setResponseMessage('SQL identity ready')
    SampleResult.setResponseData('Managed identity acquired; token is not recorded', 'UTF-8')
    SampleResult.setSuccessful(true)
} catch (IOException | IllegalStateException | NumberFormatException | JsonException error) {
    SampleResult.setResponseCode('IDENTITY_ERROR')
    SampleResult.setResponseMessage('SQL managed identity unavailable; check Engine reference identity')
    SampleResult.setSuccessful(false)
    SampleResult.setStopTest(true)
} finally {
    request.disconnect()
}
