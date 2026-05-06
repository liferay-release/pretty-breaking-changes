#! /usr/bin/python3
import sys

import git
import mistune
import os

breaking_change_report_keyword = "# breaking"

markdown = mistune.create_markdown(renderer='ast')


def get_end_of_block_using_pattern(lines, start_index, end_pattern):
    if start_index >= len(lines):
        return start_index

    end_pattern_lowercase = end_pattern.lower()

    end_of_block_index = start_index

    while end_of_block_index < len(lines):
        if not lines[end_of_block_index].lower().startswith(end_pattern_lowercase):
            end_of_block_index += 1
        else:
            break

    return end_of_block_index


def get_end_of_block(lines, start_index):
    next_line_index = get_end_of_block_using_pattern(lines, start_index, '----')

    if next_line_index == len(lines):
        next_line_index = get_end_of_block_using_pattern(lines, start_index, breaking_change_report_keyword)

    return next_line_index


def is_empty_block(lines):
    result = True

    for line in lines:
        if line.strip():
            return False

    return result


def dissect_commit_message(raw_commit_message):
    breaking_changes_info = []

    lines = raw_commit_message.split('\n')

    raw_jira_info_block_line_index = get_end_of_block_using_pattern(lines, 1, breaking_change_report_keyword)

    i = 0
    while i < raw_jira_info_block_line_index:
        if lines[i] != "":
            jira_ticket, _, jira_ticket_title = lines[i].partition(' ')
            break
        i += 1

    block_start_line_index = raw_jira_info_block_line_index

    while block_start_line_index < len(lines):
        # print(str(block_start_line_index) + " " + str(len(lines)))
        next_line_index = get_end_of_block(lines, block_start_line_index)

        # print(next_line_index)
        if next_line_index - block_start_line_index > 1 and not is_empty_block(
                lines[block_start_line_index:next_line_index]):
            info = None

            if lines[next_line_index - 1] == '----':
                block_end_line_index = next_line_index - 1
            else:
                block_end_line_index = next_line_index

            try:
                info = extract_breaking_change_info(lines[block_start_line_index:block_end_line_index])
            except Exception as e:
                print("unprocessable")
                print(e)
                print(lines)
                print("Range considered: " + str(block_start_line_index) + ":" + str(block_end_line_index))
                print()

            if info:
                breaking_changes_info.append(info)
                breaking_changes_info[-1]['jira_ticket'] = jira_ticket
                breaking_changes_info[-1]['jira_ticket_title'] = jira_ticket_title.rstrip('\r\n')

        block_start_line_index = next_line_index + 1

    return breaking_changes_info


def extract_breaking_change_info(lines):
    # print(lines)
    breaking_change_info = {}

    what_line_index = get_end_of_block_using_pattern(lines, 0, "## What")

    if what_line_index >= len(lines):
        raise LookupError('"## What" not found')

    breaking_change_info['affected_file_path'] = get_affected_file_path(lines[what_line_index])

    why_line_index = get_end_of_block_using_pattern(lines, what_line_index + 1, "## Why")

    if why_line_index >= len(lines):
        raise LookupError('"## Why" not found')

    breaking_change_info['what_info'] = "\n".join(lines[what_line_index + 1:why_line_index]).rstrip('\r\n')

    alternatives_line_index = get_end_of_block_using_pattern(lines, why_line_index + 1, "## Alternatives")

    breaking_change_info['why_info'] = "\n".join(lines[why_line_index + 1: alternatives_line_index]).rstrip('\r\n')

    if len(lines) > alternatives_line_index:
        breaking_change_info['alternatives'] = "\n".join(lines[alternatives_line_index + 1: len(lines)]).rstrip('\r\n')

    return breaking_change_info


def decorate_breaking_change_info(breaking_change_info, decoration):
    return [item | decoration for item in breaking_change_info]


def get_affected_file_path(raw_what_header):
    _, _, affected_file_path = raw_what_header.partition('## What ')

    if affected_file_path == "":
        _, _, affected_file_path = raw_what_header.partition('## what ')

    return affected_file_path


def get_first_level_path(file_path):
    first_level_path, _, remaining = file_path.partition('/')

    if len(first_level_path) == 0:
        first_level_path, _, remaining = remaining.partition('/')

    if len(remaining) == 0:
        first_level_path = 'other'

    return first_level_path


def main(repo_path, repo_branch, start_hash, end_hash):
    amendments_file_path = repo_path + "/readme/BREAKING_CHANGES_AMENDMENTS.md"

    liferay_portal_ee_repo = git.Repo(repo_path)

    print("Checkout " + repo_branch + " and pull ...")
    liferay_portal_ee_repo.git.checkout(repo_branch)
    liferay_portal_ee_repo.git.fetch('--all')
    liferay_portal_ee_repo.git.reset('--hard', 'origin/' + repo_branch)

    print("Retrieving git info ...")

    of_interest = liferay_portal_ee_repo.git.log(start_hash + ".." + end_hash, "--grep", "# breaking",
                                                 "--pretty=format:%H")
    # of_interest = liferay_portal_ee_repo.git.log("--grep", "breaking_change_report", "--pretty=format:%H")

    print("Processing git info ...")

    individual_commit_hashes = of_interest.split('\n')

    breaking_changes_info = {
        h: decorate_breaking_change_info(result, {'committed_date': liferay_portal_ee_repo.commit(h).committed_date})
        for h
        in individual_commit_hashes if (result := dissect_commit_message(liferay_portal_ee_repo.commit(h).message))}

    with open(amendments_file_path) as f:
        amendments = f.read()

    parsed = markdown.parse(amendments)
    interesting_indexes = [(i, type_of) for i in range(len(parsed)) if
                           (type_of := parsed[0][i]['type']) == 'heading' or type_of == 'block_code']

    i = 0
    while i <= len(interesting_indexes) - 2:
        if interesting_indexes[i][1] == 'heading' and interesting_indexes[i + 1][1] == 'block_code':
            git_hash = parsed[interesting_indexes[i][0]]['children'][0]['text']

            if git_hash in breaking_changes_info or (
                    liferay_portal_ee_repo.is_ancestor(start_hash, git_hash) and not liferay_portal_ee_repo.is_ancestor(
                    end_hash, git_hash)):
                amended_message = parsed[interesting_indexes[i + 1][0]]['text']
                breaking_changes_info[git_hash] = decorate_breaking_change_info(dissect_commit_message(amended_message),
                                                                                {
                                                                                    'committed_date':
                                                                                        liferay_portal_ee_repo.commit(
                                                                                            git_hash).committed_date})

                if len(breaking_changes_info[git_hash]) > 0:
                    print("Amending: " + str(breaking_changes_info[git_hash][0]['jira_ticket']))
                else:
                    print("Error processing amendment message " + git_hash)
                    print(amended_message)
                    print()

            i += 2
        else:
            i += 1

    affected_file_paths_and_hashes = {}

    for git_hash in breaking_changes_info:
        # print(hash)
        # print(breaking_changes_info[hash]['affected_file_path'])

        for change in breaking_changes_info[git_hash]:
            affected_file_path = change['affected_file_path']
            # print(affected_file_path)
            first_level_path = get_first_level_path(affected_file_path)

            if first_level_path not in affected_file_paths_and_hashes:
                affected_file_paths_and_hashes[first_level_path] = {}

            if affected_file_path in affected_file_paths_and_hashes[first_level_path]:
                affected_file_paths_and_hashes[first_level_path][affected_file_path].append(change)
            else:
                affected_file_paths_and_hashes[first_level_path][affected_file_path] = [change]

    print("Generating output ...")

    entire_output = ""

    for first_level_path in affected_file_paths_and_hashes:
        for affected_file_path in affected_file_paths_and_hashes[first_level_path]:
            file_name = os.path.basename(affected_file_path)
            this_block = f'''
    # {affected_file_path}
              
    {file_name} `{affected_file_path}`
            '''

            for change in affected_file_paths_and_hashes[first_level_path][affected_file_path]:
                this_block += '''
    * __Date:__ {committed_date}
    * __Ticket:__ [{jira_ticket}](https://liferay.atlassian.net/browse/{jira_ticket})
    * __What changed:__ {what_info}
    * __Reason:__ {why_info}
                '''.format(**change)

                if 'alternatives' in change:
                    this_block += '''
    * __Alternatives:__ {alternatives}
                    '''.format(**change)

                this_block += '''
    &nbsp;
                '''

            entire_output += this_block

    entire_output_fh = open('report.md', 'w')

    entire_output_fh.write(entire_output)

    entire_output_fh.close()


if __name__ == '__main__':
    try:
        path = sys.argv[1]
    except IndexError:
        print("Please provide a local path to the report")
        exit()

    try:
        branch = sys.argv[2]
    except IndexError:
        print("Please provide a branch name")
        exit()

    try:
        first_hash = sys.argv[3]
    except IndexError:
        print("Please provide a hash to start")
        exit()

    try:
        final_hash = sys.argv[4]
    except IndexError:
        final_hash = "HEAD"

    main(path, branch, first_hash, final_hash)
