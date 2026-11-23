# CongTyAI Local

CongTyAI Local is a prototype Windows app that runs a team of AI coding agents through a local model. A project starts with a brief and acceptance criteria. The app assigns work, tracks dependencies, and runs QA against candidate files before packaging accepted outputs.

The project explores how closely a small local model can approach frontier-model results when a frontier model helps design the architecture, task breakdown, and instructions. The implementation uses Ollama for local inference and SQLite to resume interrupted work. The interface is in Vietnamese.

With a small feature, the implementation and testing roles work from the same brief. The implementation role produces candidate files, while the QA role prepares independent checks. Those checks then run against the files that would be delivered. A larger project can split into work items with explicit dependencies.

The next milestone is to take StudyHub, a student platform for timetables, group projects, and finding study spaces, from a brief to a working ZIP built and tested by the local team.
