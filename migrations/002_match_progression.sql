-- next_match_id is already present in this project.
-- Run this migration once to let an admin choose the destination slot.
ALTER TABLE matches
    ADD COLUMN next_team_slot ENUM('team_1', 'team_2') NULL;

CREATE INDEX idx_matches_next_match_id ON matches (next_match_id);