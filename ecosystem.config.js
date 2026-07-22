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
    {
      name: "epfo-scraper",
      script: "epfo_scraper.py",
      interpreter: ".venv/bin/python",
      args: "--company-file all_companies.txt --no-search-variants --skip-existing-statuses completed,searched,skipped --delay 1.5 --details-workers 1",
      cwd: __dirname,
      autorestart: false,
      env: {
        PYTHONUNBUFFERED: "1",
      },
    },
  ],
};
