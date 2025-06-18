import re

class MessageMatcher:
    RE_AUTOLOG = [
        r"^.*(send|share|show|need)[a-zA-Z0-9\s,]{1,16}\blogs?\b.*$",
        r"^.*(where)[a-zA-Z0-9\s,]{1,32}\blogs?\b.*$",
        r"^.*(what)[a-zA-Z0-9\s,]{1,16}\blogs?\b.*(say).*$",
    ]

    def match_autolog(self, message: str) -> bool:
        return any(re.compile(regex, re.IGNORECASE).match(message) for regex in self.RE_AUTOLOG)
