-- SPVM3 API Platform: MySQL schema

CREATE TABLE IF NOT EXISTS users (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    email           VARCHAR(255) NOT NULL UNIQUE,
    name            VARCHAR(255) NOT NULL DEFAULT '',
    password_hash   VARCHAR(255) NOT NULL,
    is_admin        BOOLEAN NOT NULL DEFAULT FALSE,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    session_version INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    last_login_at   TIMESTAMP NULL DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS api_keys (
    id                 BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id            BIGINT NOT NULL,
    name               VARCHAR(255) NOT NULL,
    key_hash           VARCHAR(255) NOT NULL UNIQUE,
    key_prefix         VARCHAR(255) NOT NULL,
    environment        VARCHAR(50) NOT NULL,
    scopes             JSON NOT NULL,
    rate_limit_per_min INTEGER NOT NULL DEFAULT 60,
    expires_at         TIMESTAMP NULL DEFAULT NULL,
    last_used_at       TIMESTAMP NULL DEFAULT NULL,
    revoked_at         TIMESTAMP NULL DEFAULT NULL,
    rotated_from       BIGINT NULL DEFAULT NULL,
    created_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (rotated_from) REFERENCES api_keys(id) ON DELETE SET NULL,
    CHECK (environment IN ('live', 'test')),
    CHECK (rate_limit_per_min BETWEEN 1 AND 10000)
);
CREATE INDEX api_keys_user_idx ON api_keys (user_id);

CREATE TABLE IF NOT EXISTS knowledge (
    id         BIGINT AUTO_INCREMENT PRIMARY KEY,
    category   VARCHAR(255) NOT NULL DEFAULT 'general',
    title      VARCHAR(255) NOT NULL,
    content    LONGTEXT NOT NULL,
    tags       JSON NOT NULL,
    created_by BIGINT NULL DEFAULT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL,
    UNIQUE (category, title)
);
CREATE FULLTEXT INDEX knowledge_search_idx ON knowledge (title, content);

CREATE TABLE IF NOT EXISTS api_usage (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    api_key_id  BIGINT NOT NULL,
    endpoint    VARCHAR(255) NOT NULL,
    method      VARCHAR(50) NOT NULL,
    status_code INTEGER NOT NULL,
    request_id  VARCHAR(255),
    ip_hash     VARCHAR(255),
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (api_key_id) REFERENCES api_keys(id) ON DELETE CASCADE
);
CREATE INDEX api_usage_key_idx ON api_usage (api_key_id, created_at);
CREATE INDEX api_usage_created_idx ON api_usage (created_at);

CREATE TABLE IF NOT EXISTS audit_logs (
    id         BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id    BIGINT NULL DEFAULT NULL,
    api_key_id BIGINT NULL DEFAULT NULL,
    event_type VARCHAR(255) NOT NULL,
    request_id VARCHAR(255),
    ip_hash    VARCHAR(255),
    user_agent VARCHAR(1000),
    metadata   JSON NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (api_key_id) REFERENCES api_keys(id) ON DELETE SET NULL
);
CREATE INDEX audit_created_idx ON audit_logs (created_at);
CREATE INDEX audit_user_idx ON audit_logs (user_id, created_at);
