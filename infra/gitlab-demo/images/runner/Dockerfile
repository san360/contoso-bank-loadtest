FROM gitlab/gitlab-runner:v19.4.0@sha256:e859bcedae25987bdaf0aa79145eb5e50a0195d80a2eda7532e7a9841bb9d9a8 AS runner
FROM python:3.12.12-slim-bookworm@sha256:2986c55feb36e6cae00fa1fefb454283e4b33f35e75ff8bdd123b134130be301
ENV DEBIAN_FRONTEND=noninteractive \
    AZURE_EXTENSION_DIR=/opt/az-extensions \
    PATH=/opt/demo-python/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update -qq \
    && apt-get install --no-install-recommends -y git bash ca-certificates curl procps util-linux \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash gitlab-runner \
    && rm -f /home/gitlab-runner/.bash_logout \
    && command -v su
COPY --from=runner /usr/bin/gitlab-runner /usr/local/bin/gitlab-runner
COPY requirements-dev.txt /opt/demo-requirements.txt
RUN pip install --no-cache-dir azure-cli==2.89.0 \
    && az extension add --name load --version 2.1.0 --yes --only-show-errors \
    && chmod -R a+rX /opt/az-extensions \
    && python -m venv /opt/demo-python \
    && /opt/demo-python/bin/pip install --no-cache-dir -r /opt/demo-requirements.txt \
    && az version \
    && gitlab-runner --version \
    && /opt/demo-python/bin/python -c "import fastapi, httpx; print('Runner Python dependencies verified')"
COPY entrypoint.sh /opt/demo-entrypoint.sh
COPY configure.py /opt/configure-runner.py
RUN sed -i 's/\r$//' /opt/demo-entrypoint.sh \
    && chmod 755 /opt/demo-entrypoint.sh \
    && bash -n /opt/demo-entrypoint.sh \
    && python -m py_compile /opt/configure-runner.py
ENTRYPOINT ["/opt/demo-entrypoint.sh"]
