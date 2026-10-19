ApplicationSetting.current.update!(
  signup_enabled: false,
  password_authentication_enabled_for_web: true,
  require_admin_approval_after_user_signup: true,
  auto_devops_enabled: false,
  usage_ping_enabled: false
)

marker = '/etc/gitlab/demo-bootstrap-complete'
unless File.exist?(marker)
  user = User.find_by_username!('root')
  token = user.personal_access_tokens.build(
    name: 'demo-bootstrap',
    scopes: ['api', 'write_repository'],
    expires_at: Date.today + 7
  )
  token.set_token(ENV.fetch('GITLAB_BOOTSTRAP_TOKEN'))
  token.save!
  File.write(marker, Time.now.utc.iso8601)
  File.chmod(0600, marker)
end
puts 'Demo bootstrap complete; public signups disabled.'
