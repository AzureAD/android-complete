//  Copyright (c) Microsoft Corporation.
//  All rights reserved.
//
//  This code is licensed under the MIT License.
//
//  Permission is hereby granted, free of charge, to any person obtaining a copy
//  of this software and associated documentation files(the "Software"), to deal
//  in the Software without restriction, including without limitation the rights
//  to use, copy, modify, merge, publish, distribute, sublicense, and / or sell
//  copies of the Software, and to permit persons to whom the Software is
//  furnished to do so, subject to the following conditions :
//
//  The above copyright notice and this permission notice shall be included in
//  all copies or substantial portions of the Software.
//
//  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
//  IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
//  FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
//  AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
//  LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
//  OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
//  THE SOFTWARE.
import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';

/**
 * Design Review v5 — Single reviews.json, scoped submit, proper state management.
 *
 * Architecture:
 * - Comments stored in `.github/design-reviews/reviews.json` (dict keyed by relative spec path)
 * - Status bar button submits comments for the CURRENT file only
 * - Command palette "Submit All" submits all pending reviews
 * - Agent removes entries from reviews.json after addressing them
 * - Extension clears editor comments after submit
 */

// ─── Types ────────────────────────────────────────────────────────

interface ReviewComment {
    line: number;
    text: string;
    lineContent: string;
}

interface ReviewsFile {
    reviews: Record<string, ReviewComment[]>; // key = relative spec path
}

// ─── Constants ───────────────────────────────────────────────────

const REVIEWS_DIR = '.github/design-reviews';
const REVIEWS_FILENAME = 'reviews.json';

// ─── Main Controller ─────────────────────────────────────────────

export class DesignReviewController implements vscode.Disposable {
    private commentController: vscode.CommentController;
    private allThreads: vscode.CommentThread[] = [];
    private loadedKeys = new Set<string>(); // "relPath::line::text" prevents duplicate restore
    private statusBarItem: vscode.StatusBarItem;
    private disposables: vscode.Disposable[] = [];

    constructor(private readonly context: vscode.ExtensionContext) {
        this.commentController = vscode.comments.createCommentController(
            'orchestrator.designReview',
            'Design Review'
        );
        this.commentController.commentingRangeProvider = {
            provideCommentingRanges(document: vscode.TextDocument): vscode.Range[] {
                if (document.languageId !== 'markdown') { return []; }
                var ranges: vscode.Range[] = [];
                for (var i = 0; i < document.lineCount; i++) {
                    ranges.push(new vscode.Range(i, 0, i, 0));
                }
                return ranges;
            }
        };
        this.disposables.push(this.commentController);

        this.statusBarItem = vscode.window.createStatusBarItem(
            vscode.StatusBarAlignment.Right, 1000
        );
        this.statusBarItem.command = 'orchestrator.submitDesignReview';
        this.disposables.push(this.statusBarItem);
    }

    register(): void {
        this.disposables.push(
            vscode.commands.registerCommand('orchestrator.reviewComment.add', (reply: vscode.CommentReply) => {
                this.addComment(reply);
            })
        );
        this.disposables.push(
            vscode.commands.registerCommand('orchestrator.reviewComment.delete', (comment: EditorComment) => {
                this.deleteComment(comment);
            })
        );
        this.disposables.push(
            vscode.commands.registerCommand('orchestrator.submitDesignReview', () => {
                this.submitCurrentFile();
            })
        );
        this.disposables.push(
            vscode.commands.registerCommand('orchestrator.submitAllReviews', () => {
                this.submitAll();
            })
        );
        this.disposables.push(
            vscode.commands.registerCommand('orchestrator.clearReviewComments', () => {
                this.clearCurrentFile();
            })
        );
        this.disposables.push(
            vscode.languages.registerCodeLensProvider(
                { language: 'markdown' },
                new ReviewCodeLensProvider(this)
            )
        );
        this.disposables.push(
            vscode.window.onDidChangeActiveTextEditor(() => this.updateStatusBar())
        );
        this.disposables.push(
            vscode.workspace.onDidOpenTextDocument(doc => {
                if (doc.languageId === 'markdown') { this.restoreComments(doc); }
            })
        );

        // Restore for already-open docs
        for (var doc of vscode.workspace.textDocuments) {
            if (doc.languageId === 'markdown') { this.restoreComments(doc); }
        }

        this.updateStatusBar();
        this.context.subscriptions.push(...this.disposables);
    }

    // ─── Reviews file I/O ────────────────────────────────────────

    private getReviewsFilePath(): string | null {
        var root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
        if (!root) { return null; }
        var dir = path.join(root, REVIEWS_DIR);
        if (!fs.existsSync(dir)) { fs.mkdirSync(dir, { recursive: true }); }
        return path.join(dir, REVIEWS_FILENAME);
    }

    private readReviews(): ReviewsFile {
        var filePath = this.getReviewsFilePath();
        if (!filePath || !fs.existsSync(filePath)) {
            return { reviews: {} };
        }
        try {
            return JSON.parse(fs.readFileSync(filePath, 'utf-8'));
        } catch {
            return { reviews: {} };
        }
    }

    private writeReviews(data: ReviewsFile): void {
        var filePath = this.getReviewsFilePath();
        if (!filePath) { return; }
        // Clean up empty entries
        var keys = Object.keys(data.reviews);
        for (var i = 0; i < keys.length; i++) {
            if (data.reviews[keys[i]].length === 0) {
                delete data.reviews[keys[i]];
            }
        }
        if (Object.keys(data.reviews).length === 0) {
            // Delete file if no reviews remain
            if (fs.existsSync(filePath)) { fs.unlinkSync(filePath); }
        } else {
            fs.writeFileSync(filePath, JSON.stringify(data, null, 2), 'utf-8');
        }
    }

    private getRelativePath(uri: vscode.Uri): string {
        var root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || '';
        return path.relative(root, uri.fsPath).replace(/\\/g, '/');
    }

    // ─── Persist current file's comments to reviews.json ─────────

    private persistForFile(docUri: vscode.Uri): void {
        var relPath = this.getRelativePath(docUri);
        var data = this.readReviews();
        var comments: ReviewComment[] = [];

        for (var i = 0; i < this.allThreads.length; i++) {
            var thread = this.allThreads[i];
            if (thread.uri.toString() !== docUri.toString()) { continue; }
            for (var j = 0; j < thread.comments.length; j++) {
                var c = thread.comments[j] as EditorComment;
                comments.push({
                    line: c.line,
                    text: typeof c.body === 'string' ? c.body : (c.body as vscode.MarkdownString).value,
                    lineContent: c.lineContent,
                });
            }
        }

        data.reviews[relPath] = comments;
        this.writeReviews(data);
    }

    // ─── Add comment ─────────────────────────────────────────────

    private addComment(reply: vscode.CommentReply): void {
        var thread = reply.thread;
        var doc = vscode.workspace.textDocuments.find(
            function(d) { return d.uri.toString() === thread.uri.toString(); }
        );
        var line = thread.range?.start.line ?? 0;
        var lineContent = doc ? doc.lineAt(line).text.trim() : '';

        var comment = new EditorComment(reply.text, line, lineContent);

        thread.comments = [...thread.comments, comment];
        thread.collapsibleState = vscode.CommentThreadCollapsibleState.Collapsed;
        thread.label = thread.comments.length + ' comment' + (thread.comments.length > 1 ? 's' : '');

        if (this.allThreads.indexOf(thread) === -1) {
            this.allThreads.push(thread);
        }

        var relPath = this.getRelativePath(thread.uri);
        this.loadedKeys.add(relPath + '::' + line + '::' + reply.text);

        this.persistForFile(thread.uri);
        this.updateStatusBar();
    }

    // ─── Delete comment ──────────────────────────────────────────

    private deleteComment(commentToDelete: EditorComment): void {
        for (var i = 0; i < this.allThreads.length; i++) {
            var thread = this.allThreads[i];
            var filtered = thread.comments.filter(function(c) {
                return c !== commentToDelete;
            });

            if (filtered.length < thread.comments.length) {
                var relPath = this.getRelativePath(thread.uri);
                this.loadedKeys.delete(relPath + '::' + commentToDelete.line + '::' +
                    (typeof commentToDelete.body === 'string' ? commentToDelete.body : ''));

                if (filtered.length === 0) {
                    var uri = thread.uri;
                    thread.dispose();
                    this.allThreads.splice(i, 1);
                    this.persistForFile(uri);
                } else {
                    thread.comments = filtered;
                    thread.label = filtered.length + ' comment' + (filtered.length > 1 ? 's' : '');
                    this.persistForFile(thread.uri);
                }
                this.updateStatusBar();
                return;
            }
        }
    }

    // ─── Restore from reviews.json ───────────────────────────────

    private restoreComments(document: vscode.TextDocument): void {
        var data = this.readReviews();
        var relPath = this.getRelativePath(document.uri);
        var comments = data.reviews[relPath];
        if (!comments || comments.length === 0) { return; }

        for (var i = 0; i < comments.length; i++) {
            var rc = comments[i];
            var key = relPath + '::' + rc.line + '::' + rc.text;
            if (this.loadedKeys.has(key)) { continue; }
            this.loadedKeys.add(key);

            var line = Math.min(rc.line, document.lineCount - 1);
            var thread = this.commentController.createCommentThread(
                document.uri,
                new vscode.Range(line, 0, line, 0),
                []
            );

            var comment = new EditorComment(rc.text, rc.line, rc.lineContent);
            thread.comments = [comment];
            thread.collapsibleState = vscode.CommentThreadCollapsibleState.Collapsed;
            thread.label = '1 comment';
            this.allThreads.push(thread);
        }

        this.updateStatusBar();
    }

    // ─── Submit: current file only ───────────────────────────────

    private async submitCurrentFile(): Promise<void> {
        var editor = vscode.window.activeTextEditor;
        if (!editor) {
            vscode.window.showInformationMessage('Open a markdown file first.');
            return;
        }

        var relPath = this.getRelativePath(editor.document.uri);
        var data = this.readReviews();
        var comments = data.reviews[relPath];

        if (!comments || comments.length === 0) {
            vscode.window.showInformationMessage(
                'No review comments on this file. Click the + icon in the gutter to add.'
            );
            return;
        }

        // Build short prompt — comments stay in reviews.json for the agent to read
        var prompt = 'Use the design-reviewer skill on `' + relPath + '`.';

        // Clear editor comments for this file
        this.clearThreadsForUri(editor.document.uri);
        this.updateStatusBar();

        // Open chat
        await this.openChat(prompt);
    }

    // ─── Submit: all files ───────────────────────────────────────

    private async submitAll(): Promise<void> {
        var data = this.readReviews();
        var specPaths = Object.keys(data.reviews);

        if (specPaths.length === 0) {
            vscode.window.showInformationMessage('No pending review comments.');
            return;
        }

        var prompt = 'Use the design-reviewer skill.';

        // Clear all editor comments
        for (var i = 0; i < this.allThreads.length; i++) {
            this.allThreads[i].dispose();
        }
        this.allThreads = [];
        this.loadedKeys.clear();
        this.updateStatusBar();

        await this.openChat(prompt);
    }

    // ─── Clear: current file ─────────────────────────────────────

    private clearCurrentFile(): void {
        var editor = vscode.window.activeTextEditor;
        if (!editor) { return; }

        var docUri = editor.document.uri;
        this.clearThreadsForUri(docUri);

        // Remove from reviews.json
        var relPath = this.getRelativePath(docUri);
        var data = this.readReviews();
        delete data.reviews[relPath];
        this.writeReviews(data);

        this.updateStatusBar();
        vscode.window.showInformationMessage('Review comments cleared for this file.');
    }

    // ─── Helpers ─────────────────────────────────────────────────

    private clearThreadsForUri(docUri: vscode.Uri): void {
        var relPath = this.getRelativePath(docUri);
        var remaining: vscode.CommentThread[] = [];

        for (var i = 0; i < this.allThreads.length; i++) {
            var thread = this.allThreads[i];
            if (thread.uri.toString() === docUri.toString()) {
                // Remove loaded keys for this thread's comments
                for (var j = 0; j < thread.comments.length; j++) {
                    var c = thread.comments[j] as EditorComment;
                    var text = typeof c.body === 'string' ? c.body : '';
                    this.loadedKeys.delete(relPath + '::' + c.line + '::' + text);
                }
                thread.dispose();
            } else {
                remaining.push(thread);
            }
        }
        this.allThreads = remaining;
    }

    private async openChat(prompt: string): Promise<void> {
        try {
            await vscode.commands.executeCommand('workbench.action.chat.open', {
                query: prompt,
            });
        } catch {
            await vscode.env.clipboard.writeText(prompt);
            await vscode.commands.executeCommand('workbench.action.chat.open');
            vscode.window.showInformationMessage(
                'Review prompt copied to clipboard. Paste (Ctrl+V) in chat and send.'
            );
        }
    }

    // ─── Status Bar ──────────────────────────────────────────────

    private updateStatusBar(): void {
        var editor = vscode.window.activeTextEditor;
        if (editor && editor.document.languageId === 'markdown') {
            var count = this.getCommentCountForUri(editor.document.uri);
            if (count > 0) {
                this.statusBarItem.text = '$(comment-discussion) ' + count +
                    ' Review Comment' + (count > 1 ? 's' : '') + ' \u2014 Click to Submit';
                this.statusBarItem.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
                this.statusBarItem.tooltip = 'Submit review comments for this file to chat';
            } else {
                this.statusBarItem.text = '$(comment) Design Review';
                this.statusBarItem.backgroundColor = undefined;
                this.statusBarItem.tooltip = 'Add review comments using the + icon in the gutter';
            }
            this.statusBarItem.show();
        } else {
            this.statusBarItem.hide();
        }
    }

    private getCommentCountForUri(docUri: vscode.Uri): number {
        var count = 0;
        for (var i = 0; i < this.allThreads.length; i++) {
            if (this.allThreads[i].uri.toString() === docUri.toString()) {
                count += this.allThreads[i].comments.length;
            }
        }
        return count;
    }

    getTotalCommentCount(): number {
        var count = 0;
        for (var i = 0; i < this.allThreads.length; i++) {
            count += this.allThreads[i].comments.length;
        }
        return count;
    }

    getActiveFileCommentCount(): number {
        var editor = vscode.window.activeTextEditor;
        if (!editor) { return 0; }
        return this.getCommentCountForUri(editor.document.uri);
    }

    dispose(): void {
        for (var i = 0; i < this.disposables.length; i++) { this.disposables[i].dispose(); }
        for (var i = 0; i < this.allThreads.length; i++) { this.allThreads[i].dispose(); }
    }
}

// ─── Editor Comment class ────────────────────────────────────────

class EditorComment implements vscode.Comment {
    body: string | vscode.MarkdownString;
    mode = vscode.CommentMode.Preview;
    author: vscode.CommentAuthorInformation = { name: 'You' };
    contextValue = 'reviewComment';

    constructor(
        body: string,
        public readonly line: number,
        public readonly lineContent: string,
    ) {
        this.body = body;
    }
}

// ─── CodeLens Provider ───────────────────────────────────────────

class ReviewCodeLensProvider implements vscode.CodeLensProvider {
    constructor(private readonly controller: DesignReviewController) {}

    provideCodeLenses(document: vscode.TextDocument): vscode.CodeLens[] {
        if (document.languageId !== 'markdown') { return []; }

        var count = this.controller.getActiveFileCommentCount();
        var topRange = new vscode.Range(0, 0, 0, 0);
        var lenses: vscode.CodeLens[] = [];

        if (count > 0) {
            lenses.push(new vscode.CodeLens(topRange, {
                title: '\u{1F4AC} ' + count + ' review comment' + (count > 1 ? 's' : '') +
                    ' \u2014 Submit Review to Chat',
                command: 'orchestrator.submitDesignReview',
            }));
            lenses.push(new vscode.CodeLens(topRange, {
                title: '\u{1F5D1} Clear Comments',
                command: 'orchestrator.clearReviewComments',
            }));
        } else {
            lenses.push(new vscode.CodeLens(topRange, {
                title: '\u{1F4AC} Add review comments using the + icon in the gutter',
                command: '',
            }));
        }

        return lenses;
    }
}
