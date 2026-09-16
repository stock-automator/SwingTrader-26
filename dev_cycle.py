#!/usr/bin/env python3
"""
Development Cycle Automation
Write → Test → Commit → Push
Run this after making code changes
"""

import subprocess
import sys
from pathlib import Path
from datetime import datetime
import argparse


class DevCycle:
    """Automate the development cycle"""
    
    def __init__(self, repo_path: str = "."):
        self.repo_path = Path(repo_path)
        self.log = []
    
    def run_command(self, cmd: str, description: str = "", silent: bool = False) -> tuple[int, str, str]:
        """Run a shell command and capture output"""
        
        print(f"\n{'='*70}")
        print(f"▶ {description}")
        print(f"{'='*70}")
        
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=self.repo_path,
                capture_output=True,
                text=True
            )
            
            if not silent or result.returncode != 0:
                if result.stdout:
                    print(result.stdout)
                if result.stderr:
                    print(result.stderr)
            
            self.log.append({
                'step': description,
                'command': cmd,
                'returncode': result.returncode,
                'time': datetime.now().isoformat()
            })
            
            return result.returncode, result.stdout, result.stderr
        
        except Exception as e:
            print(f"❌ Error running command: {e}")
            return 1, "", str(e)
    
    def run_tests(self, verbose: bool = True) -> bool:
        """Run pytest"""
        
        cmd = "pytest tests/ -v --tb=short"
        if not verbose:
            cmd += " -q"
        
        returncode, stdout, stderr = self.run_command(
            cmd,
            description="Running tests (pytest)"
        )
        
        if returncode == 0:
            print("\n✅ All tests passed!")
            return True
        else:
            print("\n❌ Tests failed!")
            return False
    
    def run_linting(self) -> bool:
        """Run code quality checks"""
        
        # Black (code formatting)
        print("\n" + "="*70)
        print("▶ Checking code format with Black")
        print("="*70)
        returncode, _, _ = self.run_command(
            "black --check src/ tests/",
            description="Black format check",
            silent=True
        )
        
        if returncode != 0:
            print("⚠️  Code formatting issues found. Running auto-fix...")
            self.run_command(
                "black src/ tests/",
                description="Auto-fixing with Black"
            )
        
        # Flake8 (linting)
        print("\n" + "="*70)
        print("▶ Linting with Flake8")
        print("="*70)
        returncode, _, _ = self.run_command(
            "flake8 src/ tests/ --max-line-length=120 --exit-zero",
            description="Flake8 linting",
            silent=True
        )
        
        return True
    
    def git_status(self):
        """Show git status"""
        
        returncode, stdout, _ = self.run_command(
            "git status --short",
            description="Git status"
        )
        
        return stdout
    
    def git_commit(self, message: str = None) -> bool:
        """Commit changes"""
        
        if message is None:
            print("\nNo commit message provided. Skipping commit.")
            return False
        
        # Add all changes
        self.run_command(
            "git add -A",
            description="Staging changes"
        )
        
        # Commit
        returncode, _, _ = self.run_command(
            f'git commit -m "{message}"',
            description=f"Committing: {message}"
        )
        
        if returncode == 0:
            print("\n✅ Changes committed!")
            return True
        else:
            print("\n⚠️  Nothing to commit or commit failed")
            return False
    
    def git_push(self, branch: str = "main") -> bool:
        """Push to remote"""
        
        returncode, _, _ = self.run_command(
            f"git push origin {branch}",
            description=f"Pushing to origin/{branch}"
        )
        
        if returncode == 0:
            print(f"\n✅ Pushed to origin/{branch}!")
            return True
        else:
            print(f"\n❌ Push failed!")
            return False
    
    def full_cycle(self, commit_message: str = None, push: bool = False, branch: str = "main"):
        """Run full development cycle: format → lint → test → commit → push"""
        
        print("\n" + "="*70)
        print("🚀 STARTING DEVELOPMENT CYCLE")
        print("="*70)
        
        start_time = datetime.now()
        
        # Step 1: Linting
        print("\n[1/4] CODE QUALITY CHECK")
        self.run_linting()
        
        # Step 2: Tests
        print("\n[2/4] RUNNING TESTS")
        tests_pass = self.run_tests()
        
        if not tests_pass:
            print("\n❌ Tests failed. Aborting cycle.")
            return False
        
        # Step 3: Git commit
        if commit_message:
            print("\n[3/4] COMMITTING CHANGES")
            self.git_commit(commit_message)
        else:
            print("\n[3/4] SKIPPING COMMIT (no message provided)")
        
        # Step 4: Git push
        if push:
            print("\n[4/4] PUSHING TO REMOTE")
            self.git_push(branch)
        else:
            print("\n[4/4] SKIPPING PUSH")
        
        end_time = datetime.now()
        elapsed = (end_time - start_time).total_seconds()
        
        print("\n" + "="*70)
        print("✅ DEVELOPMENT CYCLE COMPLETE")
        print("="*70)
        print(f"⏱️  Completed in {elapsed:.1f} seconds")
        
        return True
    
    def watch_mode(self, commit_message: str = None, branch: str = "main"):
        """Watch for file changes and run cycle automatically"""
        
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
        except ImportError:
            print("❌ watchdog not installed. Install with: pip install watchdog")
            return
        
        class ChangeHandler(FileSystemEventHandler):
            def __init__(self, dev_cycle):
                self.dev_cycle = dev_cycle
                self.last_run = datetime.now()
            
            def on_modified(self, event):
                if event.is_directory:
                    return
                
                if not event.src_path.endswith(('.py', '.yml', '.yaml', '.txt', '.md')):
                    return
                
                # Debounce: only run if 5+ seconds since last run
                if (datetime.now() - self.last_run).total_seconds() < 5:
                    return
                
                self.last_run = datetime.now()
                
                print(f"\n📝 Change detected: {event.src_path}")
                self.dev_cycle.full_cycle(
                    commit_message=commit_message,
                    push=False,
                    branch=branch
                )
        
        print("👀 Watching for file changes (press Ctrl+C to stop)...")
        
        observer = Observer()
        handler = ChangeHandler(self)
        observer.schedule(handler, str(self.repo_path), recursive=True)
        observer.start()
        
        try:
            observer.join()
        except KeyboardInterrupt:
            observer.stop()
            observer.join()
            print("\n⏹️  Watch mode stopped")


def main():
    """CLI entry point"""
    
    parser = argparse.ArgumentParser(
        description="Development Cycle Automation"
    )
    
    parser.add_argument(
        'action',
        choices=['test', 'lint', 'cycle', 'watch'],
        help='Action to perform'
    )
    
    parser.add_argument(
        '-m', '--message',
        help='Commit message',
        default=None
    )
    
    parser.add_argument(
        '-p', '--push',
        action='store_true',
        help='Push to remote after cycle'
    )
    
    parser.add_argument(
        '-b', '--branch',
        default='main',
        help='Branch to push to'
    )
    
    args = parser.parse_args()
    
    dev = DevCycle()
    
    if args.action == 'test':
        success = dev.run_tests()
        sys.exit(0 if success else 1)
    
    elif args.action == 'lint':
        dev.run_linting()
        sys.exit(0)
    
    elif args.action == 'cycle':
        success = dev.full_cycle(
            commit_message=args.message,
            push=args.push,
            branch=args.branch
        )
        sys.exit(0 if success else 1)
    
    elif args.action == 'watch':
        dev.watch_mode(
            commit_message=args.message,
            branch=args.branch
        )


if __name__ == '__main__':
    main()
