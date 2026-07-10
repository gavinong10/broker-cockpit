FROM alpine:3.20
RUN apk add --no-cache postgresql16-client rclone dcron
COPY backup.sh /usr/local/bin/backup.sh
COPY crontab /etc/crontabs/root
RUN chmod +x /usr/local/bin/backup.sh
CMD ["crond", "-f", "-l", "2"]
