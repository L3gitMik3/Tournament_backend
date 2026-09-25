-- Supports generated knockout brackets while preserving existing group matches.
ALTER TABLE matches
    MODIFY team_1_id INT NULL,
    MODIFY team_2_id INT NULL,
    ADD COLUMN bracket_batch_id CHAR(36) NULL,
    ADD INDEX idx_matches_bracket_batch (bracket_batch_id);

-- Required by the admin progression selector. Safe to apply only once.
-- If this column was added by 002_match_progression.sql, skip this statement.
-- ALTER TABLE matches ADD COLUMN next_team_slot ENUM('team_1', 'team_2') NULL;