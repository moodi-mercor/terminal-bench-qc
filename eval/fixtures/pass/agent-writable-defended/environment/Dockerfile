FROM ubuntu:24.04
RUN apt-get update && apt-get install -y python3 && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /app/data /app/out
RUN printf "10\n15\n7\n" > /app/data/nums.txt
# Grader copied into the agent image (agent-writable) AND invoked by the verifier.
COPY verify.py /app/verify.py
WORKDIR /app
