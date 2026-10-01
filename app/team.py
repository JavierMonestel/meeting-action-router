"""The internal team roster (fictional). Used to resolve "get Dev to ..." to a full name,
to tell internal owners from external ones, and to brief the model."""

TEAM: dict[str, str] = {
    "Sam Rivera": "Founder & CEO",
    "Dev Patel": "CTO",
    "Priya Natarajan": "Clinical Lead",
    "Marcus Hale": "Performance Lead",
    "Javier Monestel": "Executive Assistant",
}

BY_FIRST_NAME: dict[str, str] = {name.split()[0].lower(): name for name in TEAM}
