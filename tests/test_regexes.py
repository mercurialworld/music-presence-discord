from objects import MessageMatcher

def test_autolog_regexes():
    matcher = MessageMatcher()

    should_match = [
        "Can you send your logs?",
        "please send your log file ^^",
        "send log files if you can",
        "can you share your logs?",
        "tbh i think i need more info: can you send the log file",
        "what do the logs say",
        "How can I share my logs?",
        "you can share your logs as well",
        "Where do I go for the log?",
        "and send the logs",
        "Where can I find the application logs?",
    ]
    for message in should_match:
        assert matcher.match_autolog(message) == True

    should_not_match = [
        "send your logarithm calculation",
        "I need my toilet unclogged",
        "what do the logssay",
        "show me your blog!",
    ]
    for message in should_not_match:
        assert matcher.match_autolog(message) == False
