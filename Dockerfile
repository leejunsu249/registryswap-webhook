FROM python:3.11-alpine

LABEL maintainer=jslee@mantech.co.kr

WORKDIR /app

RUN apk add --update --no-cache bind-tools

COPY requirements.txt requirements.txt

RUN pip install -r requirements.txt


COPY ./server.key /app/
COPY ./server.crt /app/
COPY ./ca.crt /app/
COPY ./registryswap.py /app/

CMD ["python", "-u", "registryswap.py"]
