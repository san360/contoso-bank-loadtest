FROM gitlab/gitlab-ce:19.4.2-ce.0@sha256:b65c3ece044d125254b4c4a99b10676bbd2502f05f8c628cb60628f44f4df11b
ENV PATH=/opt/gitlab/bin:/opt/gitlab/embedded/bin:$PATH
COPY gitlab.rb /opt/demo/gitlab.rb
COPY bootstrap.rb /opt/demo/bootstrap.rb
COPY entrypoint.sh /opt/demo/entrypoint.sh
COPY post-configure.sh /opt/demo/post-configure.sh
COPY readiness.rb /opt/demo/readiness.rb
COPY backup.sh /opt/demo/backup.sh
RUN sed -i 's/\r$//' /opt/demo/*.sh /opt/demo/*.rb \
    && chmod 755 /opt/demo/entrypoint.sh /opt/demo/post-configure.sh /opt/demo/backup.sh \
    && /opt/gitlab/embedded/bin/ruby -c /opt/demo/gitlab.rb \
    && /opt/gitlab/embedded/bin/ruby -c /opt/demo/bootstrap.rb \
    && /opt/gitlab/embedded/bin/ruby -c /opt/demo/readiness.rb \
    && bash -n /opt/demo/entrypoint.sh /opt/demo/post-configure.sh /opt/demo/backup.sh \
    && test -x /assets/init-container \
    && grep -q GITLAB_POST_RECONFIGURE_SCRIPT /assets/init-container \
    && command -v curl \
    && command -v gitlab-rails \
    && command -v gitlab-ctl \
    && command -v gitlab-backup
ENTRYPOINT ["/opt/demo/entrypoint.sh"]
