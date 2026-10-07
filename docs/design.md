# Rubricate

Rubricate grades paper assignments: quizzes, exams, worksheets, anything students write on and hand in. It follows Gradescope's flow. You create a course, create an assignment, print it, scan the stack, match names, grade one question at a time across the class, and export. If you want, a local model can transcribe responses or draft grades for you to review. Everything works without it.

Terms follow [glossary.md](glossary.md).

## What changes from Opengrader

| Opengrader | Rubricate |
|---|---|
| QA file with nested, indentation-sensitive bullets and a 1,919-line parser | Plain markdown: headings, a choice list, and two labelled lines |
| Rubric fixed to Correct / Partially Correct / Incorrect, with exclusive rows and sum rules | A list of items, each worth some points, any number of which can apply |
| Regions found by OCR, or drawn in three different editors (CLI, TUI and web) | One outline editor in the web app. You draw a box for each question's crop, Gradescope style, starting from boxes Rubricate suggests |
| OCR and five extraction routes to sort pages | Each scanned page is matched to its template page by image features |
| A workspace found by searching up from the current folder, plus a server registry, plus folder mode | One home folder holding one database |
| One grade can live in four places | One row in one database |
| Rubric scoring written in Python three times and in TypeScript once | Scoring exists only in Python. The web client shows what the server computed |
| Seven cloud AI providers, consent screens and egress logs | One local, OpenAI-compatible endpoint |
| AI suggestions tagged inside comment text | Model grades are drafts with their own status until a person confirms them |
| Names read by a vision model or OCR, then matched with no check against the runner-up | OCR guesses only rank roster students, and only clear winners get suggested |

## The flow

1. **Create a course.** Name and term. Import the roster from CSV.
2. **Create an assignment.** Paste or upload the assignment file, then fix any errors it reports.
3. **Template.** Generate a PDF for each version, or upload PDFs you made elsewhere.
4. **Outline.** Draw a box around each question's answer area on the template, as in Gradescope. The boxes start out filled in: from the layout for templates Rubricate generated, and from the printed labels for PDFs made elsewhere. Move, resize, add or delete them. A question can have more than one box.
5. **Print and collect.**
6. **Upload scans.** Pages are matched to template pages, grouped into submissions and cropped. Problems are flagged. If the course has a roster, each name and ID is matched against it and clear matches are suggested.
7. **Fix flagged submissions.** Drag pages into order or to another submission, mark a page as an extra page, split, merge or remove submissions, or delete a scan. A fix that would delete grades asks first.
8. **Match names.** Clear matches are made for you and labelled. Accept or change each one. Confirm each suggested student. Where there's no clear match, pick from the top three candidates or search the roster.
9. **Transcribe, optional.** The model reads each response and writes down what it says.
10. **Autograde, optional.** The model drafts a grade for each response.
11. **Grade.** One question at a time across every submission. Toggle rubric items, add a comment or a point adjustment, and move on. You can edit the rubric at any point, and the change applies to every grade. If you ran autograde, drafts come first, least confident first. Enter confirms a draft as it stands, and changing anything confirms it with your change.
12. **Export.** A CSV for your LMS gradebook and a feedback PDF per student. Export refuses to run while drafts remain unless you say otherwise.

Steps 9 and 10 are separate and independent. You can run either, both or neither.

## The assignment file

```markdown
---
title: "CS 101: Quiz 5"
instructions: Closed book, closed notes.
---

## What is 9 plus 6? (10 points)

Answer: 15

Rubric:
- Correct
- Off by one, such as 14 or 16 (-5)
- Shows work but no final number (-5)
- Incorrect (-10)

## What is 2 to the power of 5? (10 points)

Answer: 32

Rubric:
- Correct (+10)
- Off by one power, such as 16 or 64 (+6)
- Repeated multiplication with the wrong product (+4)

## Which of these is a sorting algorithm? (15 points)

- [ ] Dijkstra
- [x] Merge sort
- [ ] TCP

---

## Write a function that reverses a list. (20 points)

Rubric:
- Correct
- Missing the empty-list case (-3)
- Off by one in the loop (-2)
- Modifies the input instead of returning a new list (-5)
- No attempt (-20)
```

That's the whole format.

- **Questions.** `##` starts a question and `###` starts a part. Points go at the end of the heading. Everything under the heading prints, including paragraphs, code, images and lists.
- **Parts.** A question with parts is worth the sum of its parts. Points on its heading must match.
- **Code.** Fences use `` ``` `` or `~~~` and print their content as code. Headings, `Answer:`, `Rubric:`, choices and `---` inside aren't assignment syntax; `____` still makes a blank.
- **Bonus.** `(5 bonus points)` makes a question or part a bonus question. Its points don't count toward the assignment's total, so 105 out of 100 is possible.
- **Choices.** `- [ ]` is a choice and `- [x]` is the right one. Letters are added when it prints.
- **Blanks.** `____` prints as a blank to fill in. It's the only box Rubricate prints.
- **Pages.** `---` starts a new page. Questions on the same page split its empty space evenly. Choice and blank questions don't take any.
- **What doesn't print.** `Answer:` lines and the list after `Rubric:`.

**Rubric items** are checkboxes, and any number of them can apply to one response. The question above about reversing a list can lose 3 for the empty-list case and 2 for the loop at the same time. An item without a number is worth 0, which is how "Correct" marks a response as graded.

**Negative or positive scoring** is decided per question by the sign of its items. With negative scoring, minus items take points off full credit. With positive scoring, plus items add points starting from zero. In the example, "9 plus 6" uses negative scoring and "2 to the power of 5" uses positive scoring. Mixing signs in one question is an error, because the starting point would be ambiguous. Either way the score stays between 0 and the question's points.

**Versions.** One version needs nothing extra. For several, put each version's questions under `# Version A`, `# Version B` and so on, in print order. Versions share nothing. A question that appears in two versions gets copied into both, with its own answer key and rubric, and it's graded separately in each. `samples/cs101-quiz5/assignment.md` shows your sample written this way. Version B is a full copy with the questions reordered and the choices reversed. Grading one question across versions in a single pass can come later.

Version names can include spaces, punctuation and slashes. The names `.` and `..` are rejected with the version heading's line number.

The rubric in the file is where grading starts. After the first grade, the rubric lives in the app, because grades point to rubric items by id and markdown has nowhere to keep ids. Once scans are uploaded, the file can only change answer keys, points and rubric. Changing anything that would change the printed pages needs a new assignment.

## Home

Everything lives in one home folder, a `Rubricate` folder inside the user's home folder by default. Python's `Path.home()` resolves the user home folder on each platform, including Windows. CLI help shows the full native path. `RUBRICATE_HOME` or `--home` points somewhere else, for example `/srv/rubricate` on a hosted server.

```
~/Rubricate/
  rubricate.db     courses, rosters, staff, assignments, rubrics, submissions, grades, history, jobs
  files/          uploaded templates and scans, named by their hash, never modified
  cache/          page images and crops, safe to delete
  config.toml     model endpoint, Google sign-in client, admins
```

Every interface opens the same home. The web app is a view of it, and so are the CLI and the TUI. So commands work from any folder. Assignments are named `course/assignment`, like `cs101-f26/quiz-5`. File paths are only for what comes in, like scans, rosters and assignment files, and for what goes out, like exports.

The home must be on a local disk, since SQLite isn't safe on Dropbox, iCloud Drive or network shares.

A home from a newer build, or one whose tables don't match its migration number, isn't opened.

## CLI

There's one command per step of the flow. This is git's kind of Unix, not the pipe kind. Each command reads and writes the home, and commands don't hand files to each other. Opengrader's split, extract and grade steps passed folders of files down the line, and those folders became extra copies of the grades that needed merge engines. Here the only files that cross a command boundary are your inputs and your exports.

Each command calls the same function as the matching web action. `rubricate scan` and the upload button both call `scans.ingest()`, so the two can't drift apart.

### Conventions

- `COURSE` is a course slug like `cs101-f26`, and `COURSE/ASSIGNMENT` names an assignment, like `cs101-f26/quiz-5`. Slugs use lowercase letters, digits and hyphens, and an assignment can't be named `new`. The web app makes them from the course's name and term or the assignment's title.
- `QUESTION` is a printed question number, such as `3` or `4a`. In an assignment with versions, `4a` means that number in every version and `B:4a` means version B only.
- Results go to stdout. Progress and errors go to stderr, and progress bars only appear when stderr is a terminal.
- Exit code 0 means done. 1 means it failed, and anything it saved before failing stays saved, so a rerun picks up where it stopped. 2 means done, but something needs a person, such as flagged pages, unconfirmed names or drafts.

Every command accepts these:

| Option | Meaning |
|---|---|
| `--home PATH` | The home to use. Defaults to `$RUBRICATE_HOME`, then `~/Rubricate` |
| `--json` | Print the result as JSON on stdout instead of text |
| `-h`, `--help` | Show the command's help |
| `--version` | Print the version |

### The commands, in flow order

```
rubricate check     FILE
rubricate template  (FILE | COURSE/ASSIGNMENT) [-o DIR] [--version V] [--use V=PDF ...]
rubricate roster    COURSE [CSV] [--dry-run]
rubricate new       COURSE/ASSIGNMENT FILE
rubricate edit      COURSE/ASSIGNMENT [FILE]
rubricate scan      COURSE/ASSIGNMENT [PDF ...] [--workers N]
rubricate transcribe COURSE/ASSIGNMENT [--question Q ...] [--sample N] [--model NAME] [--redo]
rubricate autograde COURSE/ASSIGNMENT [--question Q ...] [--sample N] [--model NAME] [--accept-rubrics] [--redo]
rubricate grade     COURSE/ASSIGNMENT [--question Q]
rubricate export    COURSE/ASSIGNMENT [--csv PATH] [--canvas PATH] [--pdfs DIR] [--file PATH] [--include-drafts]
rubricate status    [COURSE[/ASSIGNMENT]]
rubricate serve     [--host HOST] [--port PORT] [--no-open]
```

#### `rubricate check FILE`

Checks an assignment file without touching the home. Each problem prints as `FILE:LINE: message`, followed by a summary per version: the number of questions, total points and pages. Exits 1 if there are errors. No options.

#### `rubricate template (FILE | COURSE/ASSIGNMENT)`

Writes one PDF per version.

| Option | Meaning |
|---|---|
| `-o`, `--out DIR` | Where to write the PDFs. Defaults to the current folder. Files are named `<name>-<version>.pdf` |
| `--version V` | Only this version |
| `--use V=PDF` | Use a PDF you made elsewhere for version V instead of generating one. Repeat for each version. Assignments only |

Given a file, it writes preview PDFs and nothing else. Don't print previews, because `scan` only matches against templates stored with an assignment.

Given an assignment, it stores the templates in the home and writes copies to `--out`. Those are the PDFs to print. The stored templates are made on the first run, and remade if the assignment file has changed. Once any scan exists they're fixed, and `template` only writes copies.

Every run checks that every page can be told apart from the others, and that every question's label was found. If not, it lists the pages and exits 2. You draw the missing boxes on the web app's Outline page.

#### `rubricate roster COURSE [CSV]`

With no CSV, it prints the course's roster. With a CSV, it replaces the roster, creating the course if needed.

| Option | Meaning |
|---|---|
| `--dry-run` | Show what would be added, removed and changed, without saving |

The CSV needs a header row. These columns are recognized: `sid` (or `id`, `student id`, `sis user id`), `name` (or `first name` and `last name`), `email` and `section`. Other columns are ignored. If the sid or name column is missing, it says which headers it found and exits 1. Duplicate sids are also an error. Blank lines are ignored. CSV formatting errors are reported with the row's starting line number before the roster changes.

Re-importing after adds and drops is safe. Matches are kept by sid, and a student who was matched and then left the roster stays matched and is shown as dropped.

If the roster changes between preview and confirm, the import is rejected.

#### `rubricate new COURSE/ASSIGNMENT FILE`

Checks the file and creates the assignment, creating the course if needed. If the file has errors, it prints them, creates nothing and exits 1. It also exits 1 if the assignment already exists. Use `edit` to change an existing one. No options.

#### `rubricate edit COURSE/ASSIGNMENT [FILE]`

With no FILE, it opens the stored assignment file in `$EDITOR`. On save the file is checked. If it has errors, they're printed and you choose whether to edit again or discard the change. With a FILE, it replaces the stored file without asking, and exits 1 on errors.

Once any scan exists, only answer keys, points and rubric items can change, and rubric items only until the first grade. Any other change is rejected, with the lines that caused it. No options.

To get the stored file back out, use `export --file -`.

#### `rubricate scan COURSE/ASSIGNMENT [PDF ...]`

Adds scanned PDFs. It stores them, matches each page to a template page, groups pages into submissions, crops every question, then reads names and matches them against the roster.

| Option | Meaning |
|---|---|
| `--workers N` | Processes used for page matching. Defaults to the number of CPU cores |

Adding the same PDF twice changes nothing, and a late PDF only adds its own pages. Each PDF is grouped on its own. A student split across two PDFs shows up as two flagged submissions, which you merge on the web app's Scans page. Run with no PDFs, it redoes name matching for submissions that don't have a confirmed student, for example after importing the roster.

It reports pages matched and extra per PDF, the submissions created, each flag with its scan page numbers, and how many names were suggested, need a person, or couldn't be read. It exits 2 if there are flags or names left to confirm.

#### `rubricate transcribe COURSE/ASSIGNMENT`

Optional. Has the model write down what each response says.

| Option | Meaning |
|---|---|
| `--question Q` | Only this question. Repeatable |
| `--sample N` | Only the first N responses to each question |
| `--model NAME` | Use this model for this run instead of the one in `config.toml` |
| `--redo` | Transcribe again even where a transcription exists. Text you edited by hand is kept |

Responses already transcribed with the same model are skipped. It reports, per question, how many responses were transcribed, skipped and failed. It exits 1 if no model is set up or the endpoint can't be reached.

#### `rubricate autograde COURSE/ASSIGNMENT`

Optional. Drafts grades with the model.

| Option | Meaning |
|---|---|
| `--question Q` | Only this question. Repeatable |
| `--sample N` | Only the first N responses to each question, to check the model before a full run |
| `--model NAME` | Use this model for this run instead of the one in `config.toml` |
| `--accept-rubrics` | Accept rubrics the model drafted on an earlier run, then grade with them |
| `--redo` | Draft again even where nothing has changed. Confirmed grades still aren't touched |

A question with no rubric gets a drafted rubric, printed in the report, and is skipped. You approve the draft on the web app, or rerun with `--accept-rubrics`. Confirmed grades never change. Responses already drafted with the same rubric, model and prompt are skipped.

It reports, per question, how many responses were drafted, skipped and failed, plus any rubric it drafted. It doesn't need `transcribe` to have run, but it uses transcriptions where they exist. It exits 1 if no model is set up or the endpoint can't be reached, and 2 if drafted rubrics are waiting for approval.

#### `rubricate grade COURSE/ASSIGNMENT`

Opens the TUI on the assignment.

| Option | Meaning |
|---|---|
| `--question Q` | Start on this question |

#### `rubricate export COURSE/ASSIGNMENT`

Writes grades out. It needs at least one of these:

| Option | Meaning |
|---|---|
| `--csv PATH` | Gradebook CSV with sid, name, email, section, total, then one column per question. `-` writes to stdout |
| `--canvas PATH` | The same scores in Canvas's gradebook import layout. Not yet checked against a real Canvas import |
| `--pdfs DIR` | One feedback PDF per student, named `<sid>-<name>.pdf`. It contains their scanned pages, then each question's score, rubric items and comment |
| `--file PATH` | The assignment file with the current rubric, for reuse next term. `-` writes to stdout |
| `--include-drafts` | Export even though drafts remain. Drafts count at their current score |

While drafts remain, it writes nothing and exits 2, unless you pass `--include-drafts`. Ungraded responses export as blank cells. Submissions without a confirmed student are left out. Both are listed in the report, and either one makes the exit code 2. `--file` works at any time.

#### `rubricate status [COURSE[/ASSIGNMENT]]`

With nothing, it lists every course and assignment with a line of progress each. With a course, it lists that course's assignments.

With an assignment, it shows:
- the templates;
- scans: pages, submissions and flags;
- names: confirmed, suggested and missing;
- grading per question: confirmed, draft and ungraded;
- transcriptions per question, if any;
- how often you kept the model's drafts unchanged, per question;
- the next command to run.

It always exits 0.

#### `rubricate serve`

Runs the web app until stopped. In a source checkout with no built web app, it builds one first with the package manager named in `web/package.json`, and says so.

| Option | Meaning |
|---|---|
| `--host HOST` | Address to listen on. Defaults to `$RUBRICATE_HOST`, then 127.0.0.1. Any other address needs Google sign-in set up in `config.toml`, and without it `serve` refuses to start |
| `--port PORT` | Defaults to `$RUBRICATE_PORT`, then 8765 |
| `--no-open` | Don't open a browser |

Explicit `--host` and `--port` flags take precedence over environment variables.

For frontend development, Vite proxies `/api` to `http://127.0.0.1:$RUBRICATE_PORT`, with port 8765 as the default. `RUBRICATE_API_URL` overrides the proxy destination, for example when the API runs on another machine. `RUBRICATE_HOST` only sets the API's listening address; Vite's default destination stays 127.0.0.1.

These settings read the process environment. Export shared values before starting the API and Vite in separate terminals:

```sh
export RUBRICATE_PORT=9000
uv run rubricate serve --no-open
```

```sh
export RUBRICATE_PORT=9000
pnpm --dir web dev
```

Browser requests stay relative to `/api`. The proxy settings affect development only; the packaged app serves the frontend and API together.

### Not in the CLI

Confirming names, fixing flagged submissions, drawing outline boxes, managing staff, and deleting courses or assignments are web only. The first three need you to look at the page. Staff only matters with several people. Deleting can't be undone, so it gets a confirmation screen.

## Interfaces

**Web app.** The whole flow and everything collaborative. It's built first. Local mode listens on 127.0.0.1 with no sign-in. Hosted mode requires Google sign-in, an allowed domain, and a per-course staff list of instructors and TAs.

**CLI.** The commands above, for one person.

**TUI.** Review and grading only: the question list, the crop, rubric toggles, comments, and confirming drafts. It doesn't need the server running.

**Sync.** The server and the TUI change grades through the same Python module, `grading.py`, against the same database. Every grade carries a revision. A save based on an old revision is rejected and the screen reloads, so nobody silently overwrites anyone. Both sides check SQLite's `PRAGMA data_version` every 500 ms. It changes whenever another connection commits. On a change the TUI redraws, and the server sends an event that makes the browser refetch. A TUI over ssh on the hosted machine syncs the same way. A TUI on another machine isn't supported. Collaboration across machines goes through the web.

## The model, optional

Rubricate works without a model. Nothing in the main flow needs one. If `config.toml` has no model, the web app shows the two model actions as unavailable and says how to set one up, and `transcribe` and `autograde` exit 1 with the same message.

The setting is an OpenAI-compatible base URL and a model name. Ollama at `http://localhost:11434/v1` works, and so do LM Studio, llama.cpp and vLLM. Names never use the model (see Matching names).

There are two jobs. Each runs per question or for the whole assignment, in the background with progress, and either can be run without the other.

**Transcribe.** The model reads each response's crop and writes down what it says. It's slow, at one model call per response, so run it only for the questions where it helps. It gets you:
- the text next to each crop, which you can edit;
- exact checking for questions with a key (choices, exact text, numbers), done by plain code instead of the model;
- later, grouping identical responses so one rubric choice covers the group.

**Autograde.** The model drafts a grade for each response.
1. If the question has no rubric, the model drafts one from a sample of responses. You approve or edit it before anything gets graded.
2. For questions with a key and a transcription, plain code compares the two. No model call is made.
3. Otherwise the model gets the prompt, the key, the rubric, and the transcription if there is one. If there isn't, it gets the crop. It returns the rubric items it picked, a one-line reason and a confidence.
4. Each result becomes a draft grade, with its items applied and its reason shown next to the crop.

Drafts are reviewed on the grading page. Enter confirms a draft as it stands. Changing anything confirms it with your change. Running autograde again only replaces drafts and ungraded responses, never a confirmed grade. It skips responses it already did with the same rubric, model and prompt.

Evaluation comes from the review. Per question, it shows how many drafts you confirmed unchanged and how many you changed, and it lists the changed ones. That tells you how far to trust the next run.

Handwriting is the weak spot for both jobs. Autograde gets the key and the rubric, which narrows what it has to recognize. How well that holds up on real handwriting hasn't been measured. The review numbers are how you'll find out, and `--sample` lets you try a handful before a full run.

## Scans without markers

The printed pages carry no QR codes, fiducials or boxes.

1. Render each template page at 150 dpi and compute its SIFT features once.
2. Match each scanned page against every template page with a ratio test and a RANSAC homography. The best match wins if it has at least 60 inliers and beats the runner-up by 1.5x. A page that matches nothing is an extra page, such as scratch paper.
3. The homography gives the page's identity and rotation (upside-down included), plus the transform into template coordinates.
4. Group pages into submissions by choosing the split with the lowest total cost over contiguous runs. A missing page costs 1, a repeated page 0.6, each extra version in one run 2, and each out-of-order pair 0.25. Unmatched pages attach to the submission before them.
5. Flags come from each submission's pages: missing, repeated, out of order, mixed versions, extra page.
6. Each question's crop comes from the boxes drawn on its template page. See Outline.
7. Warp each page into template coordinates and crop each question's boxes. Several boxes stack vertically into one crop. The grading page has a full-page view for responses that wander outside.

### Spike results

Run on `samples/cs101-quiz5` (8 students, 2 versions, 300 dpi handwritten scans) and its six broken variants. Scripts and output are in `docs/spike/`.

- Page identity was right for 118 of 118 pages across the 7 scans, scored against the per-page ground truth.
- The winning template beat the runner-up by 1.58x to 2.05x. The runner-up was always the same page of the other version.
- The scratch sheet got 0 inliers against every template and was flagged as an extra page. Grace's upside-down page came out at -179.6 degrees and was cropped upright.
- Median reprojection error was 0.31 to 0.53 px at 150 dpi, about 0.05 to 0.09 mm.
- Grouping matched the ground truth exactly in 5 of 7 scans. The two misses are interleaved students and page 2s swapped across versions, which page order can't untangle. Both were flagged on the right pages.
- Boxes from printed labels worked across versions. `docs/spike/band-q-nine-plus-six.png` shows "What is 9 plus 6?" cropped for all 8 students, from question 1 on version A and question 2 on version B. `docs/spike/band-q-binary-1010.png` does the same for a part.
- Matching took about 24 ms per template page per scanned page on an M4 Pro, measured. A 100-student assignment with 6 pages and 2 versions should take under 30 seconds across 12 processes (estimate).

### Known limits

- Every page needs printed content that sets it apart. Two near-blank pages that differ only in page number won't separate. The template step compares every pair of pages and warns.
- A page that's identical across versions takes its version from the rest of its submission.
- Interleaved or swapped stacks need a person, on the fix-up page.

## Outline

The outline is a set of boxes on the template pages. Each box belongs to a question, and the crop for that question on every scan is whatever falls inside its boxes once the scan is aligned to the template. That's Gradescope's outline. The boxes live only in the app. Nothing extra is printed.

A PDF page larger than 50 million pixels at 150 dpi is rejected with its page number and size.

**Editing.** The Outline page shows the version's questions in a list beside the template pages.
- Pick a question and drag on a page to draw its box.
- Drag a box to move it, and pull its edges or corners to resize it. Arrow keys nudge it.
- A question can have several boxes, for example a response that continues on the next page. Its crop stacks them in order.
- The name and ID fields get boxes the same way.
- The list shows which questions still have no box.

You can change the outline at any time, even after scanning. Crops are redone from the stored page alignments, the name and ID fields are read again, and grades stay put, because they belong to the submission and question, not the crop.

**Suggested boxes.** You start from boxes Rubricate has already placed. For a template it generated, it knows where each answer space is, because it laid out the page. For a PDF made elsewhere, it finds the printed labels:

1. Take every line of text on each page with its position, from the PDF's text layer. A page with no text layer, such as a template that was itself scanned, gets OCR'd instead. That's printed text, which OCR reads well, unlike handwriting.
2. Pick out lines that start like a label: `1.`, `1)`, `Q1`, `Question 1`, `a)`, `(a)`. The name and ID fields come from lines starting with `Name`, `ID`, `Student ID` or `SID`.
3. Line the labels up, in order, against the version's questions in the assignment file. The number, the points and how closely the text matches the heading all count. Matching in order lets stray candidates drop out, such as a numbered list inside a prompt.
4. Suggest a full-width box from each label down to the next label, or to the bottom margin. A question with parts ends where its first part starts.

Suggested boxes follow the page's rotation. A box with no positive width or height is skipped.

Suggested boxes are a head start. A layout they don't fit, like two columns, a separate answer sheet or a grid of blanks, just means drawing those boxes yourself.

For a PDF made elsewhere, the assignment file still supplies the questions, points, answer keys and rubric, and its questions must be in the PDF's printed order. `template --use` reports which questions got a suggested box and which didn't, and exits 2 if any are missing.

### Spike results

- On the cs101 templates, the text layer gave all 16 question and part labels across both versions. Boxes suggested from them cropped every student correctly (see Scans without markers).
- With the text layer ignored and the pages OCR'd as images, RapidOCR found the same 16 labels with the same text. Every position was within 1.2 pt (0.4 mm) of the text layer. The pages were clean renders, not a real scan of a blank, so a noisy scan is still untested. The script is `docs/spike/template_ocr.py`.

## Matching names

Handwriting is hard for OCR and for local models, so names are never read freely. The roster does the work.

1. RapidOCR reads the name and ID crops. Expect the reads to be wrong often.
2. Every student on the roster gets a score. IDs are compared digit by digit, and names without case or accents. A matching last name counts on its own. Both fields are tried in both orders, for students who write on the wrong line.
3. Students are assigned one to one, best scores first, so two submissions can't claim the same student.
4. A student scoring at least 0.8 and beating the runner-up by 0.3 is matched automatically. That takes the ID and the name agreeing. Automatic matches are labelled and never replace a person's choice. Each run scores them again, until a grader accepts them.
5. Otherwise a student who beats the runner-up by 0.15 is suggested, for one click to confirm. Below that, the Names page shows the top three.

Spike on cs101-quiz5. It has 8 submissions, and the roster has 10 students, two of them absent. The script is `docs/spike/names.py`.

- The OCR reads were rough. Priya came out as "Priya a Shan". Marcus wrote his ID on the name line, and it came out as "58392ρ|496". José came out as "Jose Ramiret". Alex's and Grace's IDs read as nothing.
- Opengrader's rule matched 5 of 8. That rule is 0.7 times the ID similarity plus 0.3 times the name similarity, with a 0.6 cutoff.
- The rule above matched 8 of 8. It suggested 7, all of them correct. It asked about Alex Kim, whose 7-digit partial ID also fits the absent Alex Kin, which is the case it should ask about.
- Eight students is a small sample. A real class will have near-identical names, and the runner-up margin is what keeps those as questions instead of wrong guesses.

## Data

All in `rubricate.db`:

```sql
course       (id, slug, name, term)
staff        (course, email, role)                       -- hosted mode
student      (course, sid, name, email, section)
assignment   (id, course, slug, title, source)           -- source is the assignment file
question     (id, assignment, version, parent, number, prompt, points, key, kind)
box          (question, page, x0, y0, x1, y1, position)  -- drawn on the Outline page, or suggested
template     (assignment, version, page, file)
scan_page    (id, assignment, file, page_index, version, template_page, homography, inliers)
submission   (id, assignment, version, student, suggested, suggested_score)   -- student is set only once confirmed
submission_page (scan_page, submission, template_page)   -- template_page NULL is an extra page
rubric_item  (id, question, description, points, position)
grade        (submission, question, adjustment, comment, status, confidence, reason,
              revision, updated_by, updated_at)          -- status: draft | confirmed
applied_item (submission, question, rubric_item)
transcription (submission, question, text, model, edited)       -- edited: changed by hand, kept on --redo
event        (id, at, actor, kind, data)                  -- append-only, for history and undo
job          (id, kind, assignment, state, done, total, error)
```

Each question belongs to one version. The same question in two versions is two rows, graded separately.

Scores are computed when read, never stored. With negative scoring it's `clamp(points + sum(applied) + adjustment, 0, points)`. With positive scoring it's `clamp(sum(applied) + adjustment, 0, points)`. A question with no `grade` row is ungraded. Deleting a rubric item removes it from every grade, and undo puts it back.

Uploading a scan is idempotent. Pages are keyed by file hash and page index, so the same scan twice changes nothing, and a late scan only adds its pages. Manual fixes live in the database and survive re-runs.

## Stack

**Server.** Python 3.12 with uv. FastAPI and Pydantic, so the OpenAPI schema comes from the route types. SQLite through the standard library with plain SQL, and numbered migrations tracked in `PRAGMA user_version`. PyMuPDF, OpenCV and NumPy for PDFs and scans. markdown-it-py for the assignment file. httpx for the model endpoint. RapidOCR with onnxruntime for name crops. onnxruntime telemetry is disabled. onnxruntime stopped shipping Intel Mac wheels after 1.23, so it needs the same pin Opengrader has. Authlib for Google sign-in.

**Web.** Vite, React, TypeScript, Tailwind, shadcn/ui, TanStack Router, Query and Table, and a typed client generated from the OpenAPI schema with openapi-typescript and openapi-fetch. React Compiler runs in development and production through Vite's React compiler preset and `@rolldown/plugin-babel`, automatically memoizing components and hooks. If a server response changes shape, the web build fails. The server sends an event when data changes, and TanStack Query refetches. The built app ships inside the Python package, so running Rubricate needs no Node. pnpm manages `web/`, and oxlint and oxfmt lint and format it. The components come from a shadcn preset (`base-lyra`, on Base UI).

**TUI.** Textual and textual-image, calling `grading.py` directly.

**Checks.** ruff and pyright for Python. Tests use pytest, with Playwright driven from Python so one test can work the TUI and a browser together.

The client never computes a score and never parses the assignment file. Every grade response carries its score, and every file check returns its errors with line numbers. That keeps the rules in one place, which is what Opengrader lost when it copied scoring into TypeScript.

Template generation renders the assignment file with markdown-it-py and lays it out with PyMuPDF's `Story`. I haven't tested that yet. Math in prompts is deferred.

```
src/rubricate/
  home.py         open the home, migrations
  assignment.py   parse and check the assignment file
  template.py     generate PDFs, find printed questions in a PDF
  scans.py        match pages, group submissions, crop
  names.py        read name and ID crops, match against the roster
  grading.py      rubric, grades, scores, revisions, history. The only place grades change
  model.py        endpoint client, autograde, rubric drafts
  courses.py      courses, rosters, staff
  export.py       CSV, feedback PDFs
  api/            FastAPI routes and schemas
  tui/
  cli.py
web/              the React app
```

## Cut

- OCR for responses. Only names use OCR, and only to rank roster students.
- Cloud providers, consent screens and egress logs.
- Grader handoff packets, claims, calibration and encrypted backups.
- Similarity, consistency and item-analysis reports.
- Curves, release snapshots and regrade tracking, for now.
- canvas-sak, since the CSV imports into Canvas directly.
- Desktop installers and photo import.
- The old QA format. There's no converter.

## Tests

E2E only, each leaving an artifact. `samples/cs101-quiz5` is the fixture, with `samples/cs101-quiz5/assignment.md` and its existing templates uploaded as-is.

- `test_scans` runs the original scan and the six variants. It checks page identity, grouping, flags and suggested boxes, and writes `report.json` plus a contact sheet per question. It also checks name matching: 6 automatic matches, Grace Park suggested and Alex Kim left as a question.
- `test_flow` uses Playwright through the web app, with no model set up. It creates the course, imports the roster, creates the assignment, and uploads the templates. It checks the suggested boxes, deletes one and redraws it by dragging, and checks the crop. Then it uploads the scans, fixes the flags, and matches names. Then it grades by following `ground-truth.json`, exports the CSV, and checks each total against `expected_total` (100, 92, 92, 100, 25, 68, 80, 90). The CSV is the artifact.
- `test_autograde` runs autograde against a fake OpenAI-compatible server with canned replies. It checks that `transcribe` and `autograde` each run without the other, that results land as drafts, that export refuses drafts, and that a second run never touches confirmed grades or hand-edited transcriptions. A separate manual run against real Ollama writes an agreement report.
- `test_sync` grades in the TUI with Textual's test pilot while a browser has the grading page open. It checks that the browser shows the change within 2 seconds, and that a stale save from the other side is rejected.
- `test_template` generates templates from the assignment file, fakes a filled-in scan, and checks that pages match and the suggested boxes land on the right questions.

## Build order

Web first, then the CLI, then the TUI. Each step ends in a check.

1. Scaffold: the uv project, the FastAPI app, the Vite app, client generation, the Playwright harness and the repo's CLAUDE.md. Check that Playwright loads the app and the generated client compiles.
2. Home, assignment file and scans, ported from the spike. Check `test_scans`.
3. `grading.py`.
4. Web flow with uploaded templates. Check `test_flow`.
5. Autograde, review and evaluation. Check `test_autograde`.
6. Template generation. Check `test_template`.
7. CLI.
8. TUI and sync. Check `test_sync`.
9. Hosted mode with Google sign-in.
