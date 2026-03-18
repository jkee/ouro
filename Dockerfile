FROM python:3.12-slim

# System deps: git, curl, gnupg, tini (signal handling)
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl gnupg tini \
    && rm -rf /var/lib/apt/lists/*

# Node.js 22 LTS (required for Claude Code CLI)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# GitHub CLI
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y gh \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright browser (for browse_page/browser_action tools)
RUN playwright install --with-deps chromium

# Claude Code CLI (required — sole code editing path)
RUN npm install -g @anthropic-ai/claude-code \
    && claude --version

# Pre-cache skills.sh CLI for Agent Skills support
RUN npx -y skills --version || true

# Create ouroboros OS user (bypassPermissions is blocked for root)
RUN useradd -m -s /bin/bash ouroboros

COPY . .
RUN git config --global --add safe.directory /app
RUN chown -R ouroboros:ouroboros /app

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "launcher.py"]
