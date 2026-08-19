FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl wget ca-certificates \
        nano vim htop \
        net-tools iputils-ping \
        openssh-client \
        xz-utils \
    && \
    # Install latest tmate binary from GitHub releases
    ARCH=$(uname -m) && \
    if [ "$ARCH" = "x86_64" ]; then TMATE_ARCH="amd64"; else TMATE_ARCH="arm64v8"; fi && \
    curl -sL "https://github.com/tmate-io/tmate/releases/download/2.4.0/tmate-2.4.0-static-linux-${TMATE_ARCH}.tar.xz" \
        -o /tmp/tmate.tar.xz && \
    tar -xf /tmp/tmate.tar.xz -C /tmp && \
    mv /tmp/tmate-*/tmate /usr/local/bin/tmate && \
    chmod +x /usr/local/bin/tmate && \
    rm -rf /tmp/tmate* && \
    # Cleanup
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /root

COPY start.sh /start.sh
RUN chmod +x /start.sh

CMD ["/start.sh"]
