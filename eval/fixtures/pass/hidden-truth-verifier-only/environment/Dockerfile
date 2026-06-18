FROM ubuntu:24.04
RUN apt-get update && apt-get install -y python3 && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /app/logs /app/out
RUN printf 'INFO boot\nERROR disk full\nINFO request ok\nERROR upstream timeout\nWARN slow query\nERROR oom killed\n' > /app/logs/service.log
WORKDIR /app
