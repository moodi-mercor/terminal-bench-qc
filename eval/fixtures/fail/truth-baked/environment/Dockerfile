FROM ubuntu:24.04
RUN apt-get update && apt-get install -y python3 && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /app/out && echo '2' > /opt/answer_truth.txt
WORKDIR /app
