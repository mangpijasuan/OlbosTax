"""Engine version.

Bump ``ENGINE_VERSION`` whenever calculation *behaviour* changes, including
bug fixes.  It is recorded on every stored computation so that a return filed
last April can be reproduced exactly, even after the code has moved on.

The version is separate from the rule version: the same rules can be evaluated
by a corrected engine, and the same engine can evaluate several years' rules.
Both are needed to reproduce a result.
"""

ENGINE_VERSION = "1.0.0"
