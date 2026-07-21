module.exports = {
  apps: [
    {
      name: "epfo-api",
      script: "api_server.py",
      interpreter: ".venv/bin/python",
      args: "--host 0.0.0.0 --port 8000",
      cwd: __dirname,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000,
      env: {
        PYTHONUNBUFFERED: "1",
      },
    },
  ],
};
