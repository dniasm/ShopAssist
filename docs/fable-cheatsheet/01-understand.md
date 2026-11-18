# 01 - Understand the problem

Most failed tasks fail here: a correct solution to the wrong problem.

## Questions to answer before acting

- **Who is this for, and what will they do with it?** A script for one run and
  a script for a team need different things.
- **What does done look like?** State it as something you can check: a test
  passes, a page renders, a number matches.
- **What is fixed and what is free?** Language, style, files not to touch,
  deadlines, and tools are constraints. Find them in the request, the repo
  conventions and any instruction files.
- **What did they not say that they clearly expect?** Existing tests still
  pass, the style matches nearby code, nothing unrelated changes.
- **What is the smallest thing that satisfies the request?** Start there.

## Read the request twice

On the second pass, look for:

- Words with two readings ("clean up", "fix", "update", "the config").
- Numbers and names you would otherwise paraphrase. Copy them exactly.
- Several requests in one message. List them so none is dropped.
- Things the person ruled out earlier in the conversation.

## Gather context before forming a view

- Read the code you will change and the code that calls it.
- Check how the project already solves similar problems, and copy that pattern.
- Look for instruction files, READMEs, tests and recent commits. They record
  decisions you can't see otherwise.
- When your knowledge might be out of date (library versions, APIs, prices,
  model names), look it up.

Stop gathering when another read would not change what you do next.

## Ask or assume?

Ask when the answer changes what you build, you can't find it yourself, and a
wrong guess is costly to undo.

Otherwise pick the most sensible reading, continue, and state the assumption
in your report so it is easy to correct.

A good question is specific, names the options and gives your recommendation:
"The export can be CSV or XLSX. I'd pick CSV because the importer already
reads it. OK?"

## Warning signs

- You are solving something more interesting than what was asked.
- You can't say in one sentence what the person wants.
- Your plan depends on a fact you haven't checked.
- The request looks easy and the person is experienced. You may be missing
  the hard part.
