FROM python:3.13-slim-bookworm AS wechat
COPY requirements-wechat.txt /tmp/requirements-wechat.txt
RUN pip install --no-cache-dir -r /tmp/requirements-wechat.txt

FROM mcr.microsoft.com/playwright/python:v1.56.0-noble AS dependencies
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Asia/Shanghai
WORKDIR /app
COPY --from=wechat /usr/local /opt/wechat
ENV WECHAT_PYTHON=/opt/wechat/bin/python3
COPY requirements.txt requirements-dev.txt ./
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN PYTHONHOME=/opt/wechat LD_LIBRARY_PATH=/opt/wechat/lib /opt/wechat/bin/python3 -m pip install --no-cache-dir pytest==8.4.2 \
    && pip install --no-cache-dir -r requirements-dev.txt && mkdir -p /app/data && chown pwuser:pwuser /app/data

FROM dependencies AS runtime
COPY --chown=pwuser:pwuser app ./app
COPY --chown=pwuser:pwuser tests ./tests
USER pwuser
ENTRYPOINT ["python", "-m", "app.main"]
CMD ["status"]

FROM dependencies AS debug
ARG UBUNTU_MIRROR=
RUN sed -i 's|http://|https://|g' /etc/apt/sources.list /etc/apt/sources.list.d/ubuntu.sources \
    && if [ -n "$UBUNTU_MIRROR" ]; then sed -i "s|https://archive.ubuntu.com/ubuntu|$UBUNTU_MIRROR|g; s|https://security.ubuntu.com/ubuntu|$UBUNTU_MIRROR|g" /etc/apt/sources.list /etc/apt/sources.list.d/ubuntu.sources; fi \
    && apt-get -o Acquire::Retries=3 update \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends xvfb x11vnc novnc websockify \
    && rm -rf /var/lib/apt/lists/*
COPY --chown=pwuser:pwuser app ./app
COPY --chown=pwuser:pwuser tests ./tests
COPY --chown=pwuser:pwuser webui ./webui
COPY --chmod=755 scripts/debug-browser.sh /usr/local/bin/debug-browser
USER pwuser
ENV DISPLAY=:99
ENTRYPOINT ["debug-browser"]
