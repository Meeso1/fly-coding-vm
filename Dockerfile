FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git jq openssl tmux less procps \
    && rm -rf /var/lib/apt/lists/*

# GitHub CLI (for `gh`)
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg 2>/dev/null \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/githubcli.list \
    && apt-get update && apt-get install -y gh \
    && rm -rf /var/lib/apt/lists/*

# OpenCode (installs to /root/.local/bin)
RUN curl -fsSL https://opencode.ai/install | bash
ENV PATH="/root/.local/bin:${PATH}"

# VM scripts
COPY entrypoint.sh watcher.sh /usr/local/bin/
COPY bin/mint-gh-token.sh bin/start-work.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/*.sh

# Entrypoint "project" lives in $HOME (/root): prompts are sent to a session
# there and the agent clones repos on demand into /root/work/ (rootfs is
# wiped on stop, so nothing important lives outside git remotes / /data).
COPY home/AGENTS.md /root/AGENTS.md
COPY home/opencode.jsonc /root/.config/opencode/opencode.jsonc

WORKDIR /root
CMD ["/usr/local/bin/entrypoint.sh"]
