"""One conversation: the session that runs a thread's turns, approvals and
history, and the small stores it reads. Used by the web server and the CLI,
which is why it does not live under web/ and knows nothing of HTTP: it talks to
whoever is connected through an EventSink."""
