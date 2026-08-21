FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8

# Пакеты
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl wget ca-certificates \
        nano vim htop procps psmisc \
        net-tools iputils-ping \
        openssh-client \
        xz-utils tar bash coreutils && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# tmate ставим отдельным слоем: комментарии внутри многострочного RUN
# ломали сборку на некоторых версиях docker/buildkit на VPS
RUN set -eux; \
    arch="$(uname -m)"; \
    case "$arch" in \
        x86_64)  tmate_arch="amd64" ;; \
        aarch64|arm64) tmate_arch="arm64v8" ;; \
        *) echo "unsupported arch: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://github.com/tmate-io/tmate/releases/download/2.4.0/tmate-2.4.0-static-linux-${tmate_arch}.tar.xz" \
        -o /tmp/tmate.tar.xz; \
    mkdir -p /tmp/tmate-extract; \
    tar -xJf /tmp/tmate.tar.xz -C /tmp/tmate-extract --strip-components=1; \
    install -m 0755 /tmp/tmate-extract/tmate /usr/local/bin/tmate; \
    rm -rf /tmp/tmate.tar.xz /tmp/tmate-extract; \
    tmate -V

WORKDIR /root

COPY start.sh /start.sh
RUN chmod +x /start.sh

CMD ["/start.sh"]
