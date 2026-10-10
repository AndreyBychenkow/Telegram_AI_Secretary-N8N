FROM docker.n8n.io/n8nio/n8n:latest

USER root

COPY workflow.json /home/node/workflow.json
COPY docker-entrypoint.sh /docker-custom-entrypoint.sh

RUN chmod +x /docker-custom-entrypoint.sh && \
    chown node:node /home/node/workflow.json /docker-custom-entrypoint.sh

USER node

EXPOSE 5678

ENTRYPOINT ["tini", "--", "/docker-custom-entrypoint.sh"]
