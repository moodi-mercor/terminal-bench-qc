FROM ubuntu:24.04
RUN apt-get update && apt-get install -y python3 && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /app/data /app/out
RUN printf "10\n15\n7\n" > /app/data/nums.txt
COPY verify_output.py /app/verify_output.py
# Bogus "protection": agent is root, so 400 hides nothing.
RUN chmod 400 /app/verify_output.py
WORKDIR /app
