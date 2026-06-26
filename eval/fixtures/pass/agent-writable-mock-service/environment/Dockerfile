FROM ubuntu:24.04
RUN apt-get update && apt-get install -y python3 && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /app/out
# Dir copy of a SERVICE: validate.py is a service module (verifier-named), but the
# verifier exercises the service, never invokes /opt/sms-gateway/validate.py.
COPY svc/server.py svc/validate.py /opt/sms-gateway/
WORKDIR /app
