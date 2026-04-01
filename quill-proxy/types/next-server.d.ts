declare module "next/server" {
  export class NextRequest extends Request {
    readonly headers: Headers;
    json(): Promise<any>;
  }

  export class NextResponse extends Response {
    static json(body: any, init?: ResponseInit): NextResponse;
  }
}
