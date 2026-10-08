# Templates made elsewhere

Template PDFs laid out the way graders make them in Word or Google Docs, for starting an assignment from a PDF. `make.py` writes every PDF and `expected.json`, which holds the title, page count, questions, parts and points Rubrio should find in each one. Edit the templates in `make.py` instead of the outputs.

```
uv run python samples/templates/make.py
```

| Template | What it checks |
| --- | --- |
| `points-in-label.pdf` | Points printed after each label, a question with parts, and a footer with the page count on every page, higher than the usual bottom margin. |
| `question-word.pdf` | Labels written as "Question 1 [5 pts]" with the prompt on the next line, and a page number low on the page. A prompt starts with "Name:", which must not become a name box. |
| `no-points.pdf` | No points printed anywhere, so every question needs points filled in. |
| `choices-and-parts.pdf` | A multiple-choice question with lowercase choices, which must stay one question, a question with lettered parts that print points, and name and ID fields written as underscores. |
| `numbered-lists.pdf` | Numbered instructions above question 1, and a numbered list inside question 2's prompt. Neither should become questions. |
| `points-right-margin.pdf` | Points printed at the right margin on the same line as each label. |
| `two-versions-A.pdf`, `two-versions-B.pdf` | Two versions of one quiz with the same questions in a different order. Upload both together. |
| `scanned.pdf` | The physics midterm rendered to images, so the PDF has no text layer and Rubrio finds no questions. |
