FROM ubuntu:24.04
RUN apt-get update && apt-get install -y python3 && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /app/data /app/out
RUN printf 'id,amount\n1,10\n2,15\n3,7\n' > /app/data/sales.csv
# BUG: the grader lives in the agent image (agent-writable) and tests/ runs it.
COPY verify.py /app/verify.py
WORKDIR /app
