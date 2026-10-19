require 'socket'
require 'net/http'
require 'timeout'

server = TCPServer.new('0.0.0.0', 9090)
loop do
  client = server.accept
  begin
    request = nil
    Timeout.timeout(3) do
      request = client.gets
      while request && (line = client.gets)
        break if line == "\r\n" || line == "\n"
      end
    end
    next unless request

    if request.split[1] == '/live'
      status = 200
    else
      http = Net::HTTP.new('127.0.0.1', 80, nil)
      http.open_timeout = 2
      http.read_timeout = 2
      response = http.get('/-/readiness?all=1')
      status = response.code == '200' ? 200 : 503
    end
    body = status == 200 ? "ready\n" : "unready\n"
    client.write("HTTP/1.1 #{status} #{status == 200 ? 'OK' : 'Unavailable'}\r\nContent-Length: #{body.bytesize}\r\nConnection: close\r\n\r\n#{body}")
  rescue Errno::EPIPE, Errno::ECONNRESET, Errno::ECONNREFUSED, Net::OpenTimeout, Net::ReadTimeout, Timeout::Error => error
    warn "Readiness request failed: #{error.class}"
  ensure
    client.close
  end
end
