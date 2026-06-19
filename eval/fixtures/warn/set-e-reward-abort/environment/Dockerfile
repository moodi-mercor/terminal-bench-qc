FROM ubuntu:24.04
RUN apt-get update && apt-get install -y python3 python3-pip && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /app/data /app/out
RUN printf 'the quick brown fox\n' > /app/data/in.txt
WORKDIR /app
