FROM alpine:3.20
# coreutils: GNU date for robust UTC epoch math in the scheduler loop.
# curl: Discord webhook alerts. No dcron — the loop scheduler replaces cron.
RUN apk add --no-cache postgresql16-client rclone curl coreutils
COPY backup.sh /usr/local/bin/backup.sh
COPY drill.sh /usr/local/bin/drill.sh
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/backup.sh /usr/local/bin/drill.sh /usr/local/bin/entrypoint.sh
CMD ["/usr/local/bin/entrypoint.sh"]
