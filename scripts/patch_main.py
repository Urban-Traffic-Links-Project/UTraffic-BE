"""Mount middleware patch cho main.py."""

with open('src/main.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
i = 0
while i < len(lines):
    line = lines[i]
    new_lines.append(line)
    # Sau dòng add_middleware CORSMiddleware, thêm middleware mới
    if 'allow_headers' in line and '["*"]' in line:
        # Thêm closing paren rồi middleware blocks
        i += 1
        # Expect next line is ')'
        if i < len(lines) and lines[i].strip() == ')':
            new_lines.append(lines[i])
            i += 1
            # Add blank line + middleware registration
            new_lines.append('\n')
            new_lines.append('# \u2500\u2500 API Gateway Middleware \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n')
            new_lines.append('from src.api.middleware.rate_limit import RateLimitMiddleware\n')
            new_lines.append('from src.api.middleware.request_id import RequestIDMiddleware\n')
            new_lines.append('app.add_middleware(RateLimitMiddleware)   # IP-based rate limiting (120 req/min)\n')
            new_lines.append('app.add_middleware(RequestIDMiddleware)    # X-Request-ID header cho mọi response\n')
            continue
    i += 1

with open('src/main.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)
print('SUCCESS: middleware mounted in main.py')

# Verify
with open('src/main.py', 'r', encoding='utf-8') as f:
    content = f.read()
idx = content.find('RateLimitMiddleware')
if idx >= 0:
    print('Verified: RateLimitMiddleware found in main.py')
    print(content[idx-50:idx+200])
else:
    print('FAILED: middleware not found')
