FROM debian:bookworm-slim@sha256:7ff6ba31527b9e336162fa061746c366c4b2032f4d2cff2078bea43f78efe3f8

RUN useradd --create-home user \
    && command -v bash && command -v setsid
COPY amp /usr/local/bin/amp
RUN chmod 755 /usr/local/bin/amp
USER user
WORKDIR /home/user
ENV AMP_URL=https://stale.invalid
