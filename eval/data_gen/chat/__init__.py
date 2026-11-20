"""T4.1 chat eval set: about 2,000 labelled SPECIMEN messages, written independently of ``chat/classify`` lexicons.

Labels: ``clean | abuse | scam | fake_upi | doxxing`` (plus ``doxxing_staff``: a moderator posting someone else's
private data, which must stay visible but be flagged). Every phone number, UPI id, PAN, Aadhaar-like number,
domain and handle is an obvious SPECIMEN value (see ``eval.data_gen.common.specimen``); no real person, no real
account, no link anybody should open. Abusive lines are the vocabulary a moderator has to catch, written only to
measure the classifier.
"""

GENERATOR_VERSION = "chat-1"
