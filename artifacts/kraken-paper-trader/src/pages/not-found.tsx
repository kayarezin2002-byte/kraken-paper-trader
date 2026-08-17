import { Card, CardContent } from '@/components/ui/card';
import { AlertCircle } from 'lucide-react';

export default function NotFound() {
  return (
    <div className="grid min-h-[100dvh] w-full place-items-center bg-background px-5">
      <Card className="w-full max-w-md border-border/80 bg-card shadow-[0_10px_32px_hsl(215_35%_13%_/_0.08)]">
        <CardContent className="pt-6">
          <div className="mb-4 flex gap-3">
            <AlertCircle className="h-8 w-8 text-primary" />
            <div>
              <p className="font-mono-data text-[10px] uppercase tracking-[0.16em] text-muted-foreground">navigation fault</p>
              <h1 className="mt-1 text-2xl font-extrabold tracking-tight">Page not found</h1>
            </div>
          </div>
          <p className="mt-4 text-sm leading-relaxed text-muted-foreground">That route is outside the paper-trading cockpit. Return to the dashboard to continue your review.</p>
        </CardContent>
      </Card>
    </div>
  );
}
