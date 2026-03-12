# pdfc(ompressor)

## Story :D
I built this because I kept running into the same annoying situation:<br/>
You need to submit a PDF somewhere (job application, university portal, government form, etc.), and there’s a **strict file size limit**.<br/>
So you do what everyone does:<br/>
You open a new browser windows and type: **“free pdf compressor”**<br/>
You open the first result.<br/>
It says “Free PDF compression!”<br/>
Great.<br/>
You upload your file.<br/>
It compresses it.<br/>
Then…<br/>
**“Enter your card details to download the file.”**<br/>
Every.<br/>
Single.<br/>
Time.

At that point you’ve already uploaded your document, waited for the compression, and now you’re stuck between **paying** or **starting over somewhere else**.

And there’s another problem: **PRIVACY**!

Most of those websites process your files on their servers. That means:
- your documents are uploaded to a third-party server
- you have no idea how long they store them
- you have no idea who can access them
- sometimes the files are cached or logged somewhere

If you’re compressing things like:
- CVs / resumes
- contracts
- invoices
- personal documents
- academic work

…that’s not something you necessarily want floating around on random servers.<br/>
So instead of playing **subscription roulette with random websites**, I wrote this script.

Your files never leave your computer:
- No servers.
- No accounts.
- No subscriptions.
- **No headache**

Just a simple CLI tool that uses **qpdf** and **Ghostscript** to shrink PDFs quickly and safely.<br/>
I’ve been there.
This tool is for anyone who’s also been there.

If it saves you even one **rage-inducing trip to a fake “free” PDF website**, then it did its job.<br/>
Enjoy 🚀

## Features

- Structural optimization with `qpdf`
- Image compression with `Ghostscript`
- Parallel compression
- Automatic CPU tuning
- **Folder watching**
- Progress bars
- Quality presets

## Install

Install dependencies:
```bash
brew install qpdf ghostscript
```

Install Python package:
```bash
pip install psutil tqdm colorama watchdog
```

## Use Cases

Options:
```bash
usage: pdfc.py [-h] [-q {1,2,3,4}] [-j JOBS] [-w WATCH] [--replace] [paths ...]

Compress PDFs using qpdf + Ghostscript

positional arguments:
  paths                 PDF files or folders to compress

options:
  -h, --help            show this help message and exit
  -q, --quality {1,2,3,4}
                        Compression quality (1 smallest, 4 highest)
  -j, --jobs JOBS       Parallel jobs (number or 'auto')
  -w, --watch WATCH     Watch folder for new PDFs
  --replace             Replace original PDFs instead of creating new files
```

Compress one file:

```bash
pdfc file.pdf
```

Compress many:
```bash
pdfc ~/some-path/*.pdf ./other.pdf ./other-folder
```

Set quality:
```bash
pdfc -q 1 *.pdf
```
Quality levels (mapped to Ghostscript options):
```bash
1 = smallest (/screen)
2 = balanced (/ebook)
3 = high quality (/printer)
4 = highest (/prepress)
```

Set workers manually:
```bash
pdfc -j 4 ./*.pdf
```

Watch folder:
```bash
pdfc --watch ~/Downloads
```