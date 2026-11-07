# truckbuddy.io on Railway: static files served by Caddy. .dockerignore keeps README.md and this file out of /srv.
FROM caddy:2.8-alpine
COPY . /srv
RUN mv /srv/Caddyfile /etc/caddy/Caddyfile
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
