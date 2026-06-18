FROM ubuntu:24.04
RUN apt-get update && apt-get install -y python3 && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /app/out && printf 'id,status\n1,ok\n3,ok\n' > /opt/expected_output.csv
WORKDIR /app
