FROM ubuntu:24.04
RUN apt-get update && apt-get install -y python3 python3-pip sqlite3 \
    && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /app/data /app/out
RUN printf 'id,status\n1,ok\n2,void\n3,ok\n' > /app/data/records.csv
WORKDIR /app
