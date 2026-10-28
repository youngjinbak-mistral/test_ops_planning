FROM python:3.11-slim

WORKDIR /app
COPY planner ./planner

ENV PLANNER_HOST=0.0.0.0

EXPOSE 8000
CMD ["python3", "-m", "planner.cli", "web"]
