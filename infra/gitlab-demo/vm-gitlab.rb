external_url ENV.fetch('GITLAB_EXTERNAL_URL')
letsencrypt['enable'] = false
nginx['listen_port'] = 80
nginx['listen_https'] = false
nginx['proxy_set_headers'] = {
  'Host' => '$http_host',
  'X-Real-IP' => '$remote_addr',
  'X-Forwarded-For' => '$proxy_add_x_forwarded_for',
  'X-Forwarded-Proto' => 'https',
  'X-Forwarded-Ssl' => 'on'
}
gitlab_rails['usage_ping_enabled'] = false
gitlab_rails['backup_keep_time'] = 604800
puma['worker_processes'] = 2
sidekiq['concurrency'] = 5
prometheus_monitoring['enable'] = false
gitlab_kas['enable'] = false
registry['enable'] = false
gitlab_pages['enable'] = false
