FROM python:3.12-slim

WORKDIR /app

# Dependencies first, so code edits don't reinstall them
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ft2_agent ./ft2_agent

# Run as a non-root user
RUN useradd --create-home --uid 10001 agent
USER agent

EXPOSE 8000

CMD ["uvicorn", "ft2_agent.app:app", "--host", "0.0.0.0", "--port", "8000"]
