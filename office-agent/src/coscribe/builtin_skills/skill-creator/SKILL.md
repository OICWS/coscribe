---
name: Skill Creator
description: Interview the user and write a new Skill (a SKILL.md coscribe can load later) to capture a capability, house style, or piece of knowledge worth reusing -- load when the user asks to save/remember how to do something as a skill, create a skill, or teach coscribe a repeatable procedure.
---
Mirrors Claude Code's own bundled skill-creator: turns a capability worth
repeating into a new Skill, written to your local skills directory (its
exact path is in your base instructions, right after the "Available
skills" listing) via `write_file` -- the same tool, same approval gate,
you'd use for any other file. This skill's own job is to run the
interview and produce good frontmatter/content, not to invent new
machinery.

## 1. Understand what's actually worth saving

Don't start writing a SKILL.md from a one-line request. Get clear on:
- What triggers this skill -- what request should make you load it later?
- What's the actual procedure, knowledge, or house style being captured?
- Is this genuinely reusable, or a one-off that doesn't need to survive
  past this conversation?

If the shape of it is genuinely ambiguous -- multiple plausible triggers,
or unclear how narrow/broad it should be -- ask, including with
`ask_user_question` if a short list of concrete options would resolve it
faster than an open-ended question. Don't invent scope the user didn't
ask for.

## 2. Check for overlap first

Your "Available skills" listing (in your own instructions, always
visible) already names every existing skill and when to use it -- both
built-in (Word/Excel/PowerPoint) and the user's own. Read it before
writing anything new:
- A close match already exists → prefer *editing* that skill instead of
  creating a near-duplicate (`read_file` its `SKILL.md`, then
  `write_file` the revised content back over it). Two skills with
  overlapping trigger conditions just make it more likely the wrong one
  loads, or neither does.
- Genuinely new territory → continue below.

## 3. Design the frontmatter

Two fields, both required, both plain YAML strings (no nested structure):

- `name`: short, human-readable, Title Case (e.g. "Weekly Report Format",
  not "weekly_report_format" or "Formats weekly reports for the team").
  This is a label, not a sentence.
- `description`: the field that actually matters. It's the *only* thing
  visible to you before this skill is loaded (progressive disclosure --
  full body loads on demand via `load_skill`), so it has to describe
  *when to use this skill* clearly enough that a future you, reading only
  this one line among a dozen others, reliably picks it at the right
  moment and skips it otherwise. State the trigger condition plainly --
  "load before/when doing X" -- not a vague summary of the content.

  Weak: "Helps with reports." (helps how? which reports? when?)
  Strong: "How to format the team's weekly status report -- load when
  asked to write or update the weekly report, not for other report
  types."

## 4. Write the body

The instructions or knowledge itself, in Markdown. Match this codebase's
existing built-in skills' register (see e.g. the Word/Excel/PowerPoint
skills already loaded alongside this one) -- deeper, more specific
guidance than what's already in your base instructions, not a restatement
of things you already know how to do. No meta-commentary about the skill
itself (don't narrate "this skill teaches you to..." inside the body --
the description already says that); write it as direct guidance to your
future self.

If the skill genuinely needs bundled reference material (not something to
*run* -- you have no code-execution tool, so a script placed here can be
read via `read_skill_file` but never executed), additional files can go
in the same skill directory alongside `SKILL.md` and be referenced from
the body by relative path.

## 5. Save it

Pick a kebab-case directory slug from the skill's name (e.g. "weekly-
report-format"), then `write_file` the full frontmatter+body to
`<skills directory>/<slug>/SKILL.md` -- the exact skills-directory path
is in your base instructions. Show the user the actual content before
(or as) you write it; this is exactly the kind of thing worth a plain-
language summary of what you're about to save, same as any other write.

## 6. After saving

Tell the user it's saved and will show up in "Available skills" starting
next turn (skills are loaded once per agent build, not hot-reloaded mid-
turn). If they want to remove a skill later, `delete_file` its `SKILL.md`
(it only removes single files, not the directory itself -- deleting the
`SKILL.md` is enough, since a directory without one isn't recognized as a
skill at all) -- mention it if asked, don't do it unprompted.
