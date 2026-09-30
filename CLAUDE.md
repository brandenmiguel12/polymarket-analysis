
## Installing skills

When the user asks to install or download a skill, they want it in their
claude.ai skills library so it is available in every session, not only this
repo. For each skill: review its scripts, install it into `.claude/skills/`,
package each skill folder as its own `.zip` (the folder at the zip root), and
send the zips to the user with steps to upload them in claude.ai
(Customize → Skills → upload a skill). Check `ListSkills` first and skip
skills already in the library.
