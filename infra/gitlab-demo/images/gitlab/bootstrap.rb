mode = ARGV.fetch(0)
unless ['create-token', 'ensure-local-auth'].include?(mode)
  raise ArgumentError, 'Unsupported demo bootstrap mode'
end

ApplicationSetting.current.update!(
  signup_enabled: false,
  password_authentication_enabled_for_web: true,
  require_admin_approval_after_user_signup: true,
  auto_devops_enabled: false,
  usage_ping_enabled: false
)

if mode == 'create-token'
  user = User.find_by_username!('root')
  value = ENV.fetch('GITLAB_BOOTSTRAP_TOKEN')
  token = PersonalAccessToken.find_by_token(value)
  if token
    unless token.user_id == user.id && !token.revoked? && !token.expired? &&
        ['api', 'write_repository'].all? { |scope| token.scopes.include?(scope) }
      raise 'Existing bootstrap token is not usable; explicit credential recovery is required'
    end
  else
    token = user.personal_access_tokens.build(
      name: 'demo-bootstrap',
      scopes: ['api', 'write_repository'],
      expires_at: Date.today + 7
    )
    token.set_token(value)
    token.save!
  end
end
puts 'Demo bootstrap complete; public signups disabled.'
