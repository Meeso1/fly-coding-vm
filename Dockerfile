FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git jq openssl tmux less procps python3 \
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

# uv + the vm-tools CLI project (git submodule at vm-tools/, installed as the
# `vm-tools` command). The VM's OpenCode skills instruct the agent to run it.
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
COPY vm-tools /opt/vm-tools
RUN uv tool install /opt/vm-tools

# VM scripts (entrypoint + watcher stay bash)
COPY entrypoint.sh watcher.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/*.sh

# Global OpenCode skills for the VM — available in every session/project,
# including /root/work/<repo>; repo-local .opencode/skills override by ID.
COPY skills /root/.config/opencode/skills

# Entrypoint "project" lives in $HOME (/root): prompts are sent to a session
# there; agents clone repos on demand into /root/work/ via the start-repo-work
# skill (rootfs is wiped on stop, so nothing important lives outside git
# remotes / /data).
COPY home/AGENTS.md /root/AGENTS.md
COPY home/opencode.jsonc /root/.config/opencode/opencode.jsonc

WORKDIR /root
CMD ["/usr/local/bin/entrypoint.sh"]
