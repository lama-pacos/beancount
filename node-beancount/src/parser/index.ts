import { Transaction, Account, Meta } from '../types';

export class Parser {
    private content: string;
    private filename: string;
    private currentLine: number = 0;
    private lines: string[];

    constructor(content: string, filename: string = '<string>') {
        this.content = content;
        this.filename = filename;
        this.lines = content.split('\n');
    }

    private createMeta(lineNo: number, original?: string): Meta {
        return {
            filename: this.filename,
            lineno: lineNo,
            original
        };
    }

    private parseDate(dateStr: string): Date {
        const match = dateStr.match(/^(\d{4})-(\d{2})-(\d{2})$/);
        if (!match) {
            throw new Error(`Invalid date format: ${dateStr}`);
        }
        return new Date(parseInt(match[1]), parseInt(match[2]) - 1, parseInt(match[3]));
    }

    private parseTransaction(line: string, lineNo: number): Transaction {
        // Basic transaction parsing
        const match = line.match(/^(\d{4}-\d{2}-\d{2})\s+([*!])\s+(?:"([^"]*)")?\s*(.*)$/);
        if (!match) {
            throw new Error(`Invalid transaction format at line ${lineNo}: ${line}`);
        }

        const [_, dateStr, flag, payee, narration] = match;

        return {
            type: 'transaction',
            date: this.parseDate(dateStr),
            meta: this.createMeta(lineNo, line),
            flag,
            payee: payee || undefined,
            narration: narration.trim(),
            tags: new Set(),
            links: new Set(),
            postings: []
        };
    }

    private parseAccount(line: string, lineNo: number): Account {
        const match = line.match(/^(\d{4}-\d{2}-\d{2})\s+open\s+([A-Z][A-Za-z0-9:]+)(?:\s+([A-Z,]+))?$/);
        if (!match) {
            throw new Error(`Invalid account format at line ${lineNo}: ${line}`);
        }

        const [_, dateStr, name, currencies] = match;

        return {
            type: 'account',
            date: this.parseDate(dateStr),
            meta: this.createMeta(lineNo, line),
            name,
            currencies: currencies ? currencies.split(',') : undefined
        };
    }

    parse(): (Transaction | Account)[] {
        const entries: (Transaction | Account)[] = [];
        
        for (let i = 0; i < this.lines.length; i++) {
            const line = this.lines[i].trim();
            this.currentLine = i + 1;

            if (!line || line.startsWith(';')) {
                continue;
            }

            try {
                if (line.match(/^\d{4}-\d{2}-\d{2}\s+[*!]/)) {
                    entries.push(this.parseTransaction(line, this.currentLine));
                } else if (line.match(/^\d{4}-\d{2}-\d{2}\s+open/)) {
                    entries.push(this.parseAccount(line, this.currentLine));
                }
                // More directive types will be added here
            } catch (e) {
                console.error(`Error parsing line ${this.currentLine}: ${e.message}`);
                throw e;
            }
        }

        return entries;
    }
}
