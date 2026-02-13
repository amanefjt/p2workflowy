# p2workflowy Web

A web-based version of p2workflowy that converts paper images into Workflowy-formatted text using Google Gemini API.

## Features
- **Image to Text**: Uses Gemini Vision to extract and structure text from images.
- **Workflowy Format**: Automatically formats text with correct indentation.
- **Translation**: Translates content to Japanese (or specified language) using custom dictionaries.
- **Custom Dictionaries**: Upload your own CSV/TXT glossaries for consistent translation.
- **Secure**: API Key is stored only in your browser's local storage.

## Setup
1.  Install dependencies:
    ```bash
    npm install
    ```
2.  Start development server:
    ```bash
    npm run dev
    ```
3.  Build for production:
    ```bash
    npm run build
    ```

## Deployment
This project is built with Vite and can be easily deployed to Cloudflare Pages, Vercel, or Netlify.
Output directory is `dist`.
